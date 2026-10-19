"""真实阶段处理器：把 M1→M5 接进 TravelApplicationService（Phase B）。

职责边界：
- 本处理器只做编排与 typed 映射，不直接选择工具/执行器；
- DIRECT 走无工具单轮回答；SIMPLE/COMPLEX 走 PlanningService→Runtime（ReAct+工具）；
- 不伪造行程：COMPLEX 执行成功但尚未接入行程合成（Phase D）时返回
  failed + TRIP_PLAN_SYNTHESIS_NOT_READY（研究结果与事件真实可查）。
"""

from __future__ import annotations

import logging
from contextlib import nullcontext
from typing import Any

from ..runtime.graph_runtime import ExecutionOwnership
from ..telemetry import LLMCallContext
from ..domain import (
    ConversationState,
    ExecutionRoute,
    IntentTurnResult,
    IntentTurnStatus,
    PlanningContext,
)
from .travel_service import RunRecord, StageOutcome, ResultKind
from .intent_service import IntentService
from .routing_service import RoutingService
from .planning_service import PlanningService
from .events import StageEvents

log = logging.getLogger(__name__)


class TravelPipelineProcessor:
    def __init__(
        self,
        *,
        intent_service: IntentService,
        routing_service: RoutingService,
        planning_service: PlanningService,
        runtime,
        llm,
        capabilities: set[str],
        direct_max_tokens_hint: int = 300,
        synthesizer=None,
        evaluator=None,
        replanning_service=None,
        memory_service=None,
        execution_budget=None,
        max_repair_attempts: int = 1,
    ):
        self.intent_service = intent_service
        self.routing_service = routing_service
        self.planning_service = planning_service
        self.runtime = runtime
        self.llm = llm
        self.capabilities = capabilities
        self.direct_max_tokens_hint = direct_max_tokens_hint
        # Phase D：COMPLEX 成功后把 TaskResults 合成为 TripPlan（不调用工具）
        self.synthesizer = synthesizer
        # Phase E：对合成结果做只读质量评估（区分“执行完成”与“满足用户要求”）
        self.evaluator = evaluator
        # Phase F：执行预算护栏 + 局部重规划 vN+1
        self.replanning_service = replanning_service
        self.memory_service = memory_service
        self.execution_budget = execution_budget
        self.max_repair_attempts = max_repair_attempts

    async def process(self, message: str, run: RunRecord) -> StageOutcome:
        session_id = run.run_id
        ownership = (ExecutionOwnership(user_id=run.user_id, run_id=run.run_id, session_id=session_id)
                     if run.user_id else None)
        events = StageEvents()
        prior_state = ConversationState.model_validate(run.conversation) if run.conversation else None
        prior_plan = prior_state.active_plan if prior_state else None
        prior_revision = prior_state.plan_revision if prior_state else 0
        memory_context = await self.memory_service.context_for(run.user_id, message) if self.memory_service else []
        with self._llm_scope("INTENT", run):
            conversation, turn = await self.intent_service.handle_message(
                message, state=prior_state,
                session_id=session_id, memory_context=memory_context,
            )
        run.conversation = conversation.model_dump(mode='json')
        if self.memory_service:
            await self.memory_service.remember_explicit_fields(run.user_id, turn.explicit_fields, run.run_id)
        status = turn.status
        events.append(
            {
                "event": "intent_status",
                "payload": {
                    "status": status.value,
                    "planning_intent": turn.intent.planning_intent,
                },
            }
        )

        if status is IntentTurnStatus.OUT_OF_DOMAIN:
            return StageOutcome(
                kind=ResultKind.FAILED,
                message="这个问题不在旅行规划范围内，无法为你生成旅行计划。",
                error_code="OUT_OF_DOMAIN",
                events=events,
            )
        if status is IntentTurnStatus.CONFLICT:
            events.append({"event": "intent_conflict", "payload": {"conflicts": turn.analysis.conflicts}})
            return StageOutcome(
                kind=ResultKind.CLARIFICATION,
                message="；".join(turn.analysis.conflicts) or "需求存在冲突，需要澄清。",
                questions=[],
                error_code="INTENT_CONFLICT",
                events=events,
            )
        if status is IntentTurnStatus.NEEDS_CLARIFICATION:
            questions = [q.model_dump(mode="json") for q in turn.questions]
            events.append(
                {"event": "clarification_needed", "payload": {"count": len(questions)}}
            )
            return StageOutcome(
                kind=ResultKind.CLARIFICATION,
                message="为了给出可用的行程规划，还需要补充以下信息。",
                questions=questions,
                error_code=None,
                events=events,
            )

        # A HumanApproval node is a durable graph pause, not a blocking input().
        store = getattr(self.runtime, "store", None)
        paused = await store.load(session_id, user_id=run.user_id) if store is not None else None
        paused_revision_message = None
        if paused is not None and paused.current_status == "waiting_for_user":
            if message.strip().lower() in {"确认", "同意", "approve", "approved", "继续"}:
                state = await self.runtime.approve(paused, ownership=ownership)
                events.append({"event": "human_approval_resolved", "payload": {"decision": "approved"}})
                return await self._synthesize_and_evaluate(turn.intent, state, events, prior_revision=prior_revision, ownership=ownership)
            paused_revision_message = message
            events.append({"event": "conditional_edge_selected", "payload": {"condition": "revise", "source_task_id": (paused.metadata.get("pending_approval") or {}).get("task_id")}})

        # READY：进入路由
        with self._llm_scope("ROUTER", run):
            decision = await self.routing_service.route(turn)
        events.append(
            {
                "event": "route_selected",
                "payload": {
                    "route": decision.route.value,
                    "execution_profile": decision.execution_profile.value,
                    "max_research_agents": decision.execution_profile.max_research_agents,
                    "complexity_score": decision.complexity_score,
                    "reasons": decision.reasons,
                },
            }
        )
        if decision.route is ExecutionRoute.DIRECT:
            return await self._direct_answer(turn, events, run)

        if prior_plan and self._is_plan_question(message):
            return await self._answer_plan_question(message, prior_plan, prior_revision, events)
        if prior_plan:
            from .revision_scope import extract_requested_places
            aliases = turn.intent.metadata.get("entity_aliases", {})
            explicit_places = [aliases.get(place, place) for place in extract_requested_places(message)]
            if explicit_places:
                turn.intent.must_have = list(dict.fromkeys([*turn.intent.must_have, *explicit_places]))
                turn.intent.constraints["revision_required_places"] = explicit_places
            events.append({"event": "plan_revision_requested", "payload": {"previous_revision": prior_revision}})
        return await self._run_planned(decision, turn, session_id, events, prior_revision=prior_revision,
                                       revision_message=(message if prior_plan else paused_revision_message),
                                       ownership=ownership, user_id=run.user_id)

    def _llm_scope(self, stage: str, run: RunRecord | None = None, *, user_id: str | None = None, run_id: str | None = None, graph=None, task_id: str | None = None):
        scope = getattr(self.llm, "scope", None)
        if scope is None:
            return nullcontext()
        return scope(LLMCallContext(stage=stage, user_id=(run.user_id if run else user_id), run_id=(run.run_id if run else run_id), graph_id=(graph.graph_id if graph else None), graph_version=(graph.version if graph else None), task_id=task_id))

    @staticmethod
    def _is_plan_question(message: str) -> bool:
        """Fast, conservative split: questions explain the active plan without re-research."""
        text = message.strip()
        question_terms = ("为什么", "依据", "来源", "怎么去", "什么意思", "解释", "为何", "？", "?")
        edit_terms = ("改", "换", "删除", "不要", "增加", "调整", "预算", "改成", "替换")
        return any(term in text for term in question_terms) and not any(term in text for term in edit_terms)

    async def _answer_plan_question(self, message, plan, revision, events) -> StageOutcome:
        """Answer against saved plan evidence; do not start a new research graph for a follow-up."""
        compact = {
            "city": plan.get("city"), "days": plan.get("days", [])[:7],
            "sources": plan.get("sources", {}), "assumptions": plan.get("assumptions", []),
        }
        try:
            response = await self.llm.decide(
                None, [], tools=[], dependency_results={}, messages=[
                    {"role": "system", "content": "Answer the user's question using only the saved travel plan and its cited evidence. Do not invent facts, do not call tools, and say when the plan lacks the requested evidence."},
                    {"role": "user", "content": f"question={message}\nactive_plan={compact}"},
                ],
            )
        except Exception as error:
            return StageOutcome(kind=ResultKind.FAILED, message=f"行程追问失败：{error}", error_code="PLAN_QUESTION_FAILED", events=events)
        content = str(response.get("final") or "").strip()
        if not content:
            return StageOutcome(kind=ResultKind.FAILED, message="未能基于当前行程生成回答。", error_code="PLAN_QUESTION_EMPTY", events=events)
        events.append({"event": "plan_question_answered", "payload": {"plan_revision": revision, "used_tools": 0}})
        return StageOutcome(kind=ResultKind.ANSWER, message=content,
                            data={"scope": "plan_question", "plan_revision": revision, "not_an_itinerary": True}, events=events)

    # ---- DIRECT：无工具、有界单轮回答 ----

    async def _direct_answer(
        self, turn: IntentTurnResult, events: list[dict[str, Any]], run: RunRecord
    ) -> StageOutcome:
        intent = turn.intent
        system = (
            "你是旅行信息助手。请只回答用户的这一个问题（如天气/开放时间/比较/可行性等），"
            "不要编造行程，不要承诺这不是真实规划结果；信息不足时明确说明。"
            "如果没有真正调用工具，绝不声称'我查询了/根据工具/根据实时数据'之类来源表述；"
            "基于常识的知识性回答请直接给出，并注明是常识信息。"
        )
        facts = intent.model_dump(mode="json")
        messages = [
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"query={intent.raw_query}\nnormalized_intent={facts}",
            },
        ]
        try:
            with self._llm_scope("DIRECT", run):
                decision = await self.llm.decide(
                    None,
                    [],
                    messages=messages,
                    tools=[],
                    dependency_results={},
                )
        except Exception as error:
            return StageOutcome(
                kind=ResultKind.FAILED,
                message=f"DIRECT 回答失败：{type(error).__name__}: {error}",
                error_code="DIRECT_ANSWER_FAILED",
                data={'diagnostics': [error.diagnostic]} if getattr(error, 'diagnostic', None) else None,
                events=events,
            )
        content = decision.get("final")
        if not content or not str(content).strip():
            return StageOutcome(
                kind=ResultKind.FAILED,
                message="模型未返回有效回答。",
                error_code="DIRECT_ANSWER_EMPTY",
                events=events,
            )
        events.append({"event": "answer_ready", "payload": {"scope": "direct"}})
        return StageOutcome(
            kind=ResultKind.ANSWER,
            message=str(content),
            data={"scope": "direct", "not_an_itinerary": True},
            events=events,
        )

    # ---- SIMPLE / COMPLEX：规划 + Runtime 执行 ----

    async def _run_planned(self, decision, turn, session_id, events, prior_revision=0,
                           revision_message: str | None = None, ownership=None, user_id: str | None = None) -> StageOutcome:
        intent = turn.intent
        context = PlanningContext(
            session_id=session_id,
            intent=intent,
            assumptions=turn.assumptions,
            available_capabilities=self.capabilities,
            metadata={"execution_profile": decision.execution_profile.value,
                      "max_research_agents": decision.execution_profile.max_research_agents,
                      "research_task_budget": decision.execution_profile.max_research_agents,
                      "worker_concurrency_budget": decision.execution_profile.max_concurrent_workers,
                      "public_transit_supported": False,
                      "requires_human_approval": any(term in intent.raw_query for term in ("确认后", "让我确认", "先确认", "候选后确认")),
                      "agent_profiles": {"hotel_agent": ["accommodation_research", "geospatial_reasoning"], "poi_agent": ["place_research"], "transport_agent": ["transport_research", "geospatial_reasoning"], "weather_agent": ["weather_research"], "itinerary_agent": ["itinerary_synthesis"]}},
        )
        # Phase F：规划→执行→（失败则局部重规划 vN+1）→成功退出；带执行预算护栏
        graph: Any = None
        state: Any = None
        prev_graph: Any = None
        prev_state: Any = None
        revision_base_state: Any = None
        revision_researched_task_ids: list[str] = []
        attempt = 0
        # An itinerary edit is an incremental replan, not a fresh planner call.
        # Reuse the durable vN checkpoint and only invalidate the agents whose
        # evidence can be affected by the user feedback, plus their downstreams.
        if revision_message and self.replanning_service is not None:
            store = getattr(self.runtime, "store", None)
            prior_state = await store.load(session_id, user_id=user_id) if store is not None else None
            if prior_state is not None and prior_state.current_status in {"succeeded", "waiting_for_user"}:
                from .revision_scope import select_revision_targets
                direct_targets = select_revision_targets(prior_state.graph, revision_message)
                target_ids = self.replanning_service.compute_repair_scope(prior_state.graph, direct_targets)
                repair_hints = {task_id: [f"用户本轮修改：{revision_message[:300]}"] for task_id in direct_targets}
                repaired = self.replanning_service.build_repair_graph(
                    prior_state.graph, target_ids, repair_hints, context
                )
                if repaired.success and repaired.graph is not None:
                    graph = repaired.graph
                    state = self.replanning_service.merge_state(prior_state, graph, target_ids)
                    revision_base_state = prior_state
                    revision_researched_task_ids = [
                        task_id for task_id in target_ids
                        if "itinerary_synthesis" not in graph.task(task_id).required_capabilities
                    ]
                    events.append({"event": "agent_revision_requests", "payload": {
                        "previous_version": prior_state.graph.version,
                        "next_version": graph.version,
                        "message": revision_message[:300],
                        "recipient_task_ids": direct_targets,
                        "rerun_task_ids": target_ids,
                        "reused_task_ids": [tid for tid, status in state.task_statuses.items() if status.value == "succeeded"],
                    }})
        while True:
            if attempt == 0 and graph is None:
                with self._llm_scope("PLANNER", user_id=user_id, run_id=session_id):
                    result = await self.planning_service.plan(intent, decision, context)
                if not result.success or result.graph is None:
                    events.append(
                        {"event": "plan_failed", "payload": {"errors": result.errors}}
                    )
                    return StageOutcome(
                        kind=ResultKind.FAILED,
                        message="规划失败：" + ("；".join(result.errors) if result.errors else "未知原因"),
                        error_code="PLAN_FAILED",
                        data=result.metadata or None,
                        events=events,
                    )
                graph = result.graph
            elif attempt > 0:
                replan = self.replanning_service.build_repair_graph(
                    prev_graph=prev_graph,
                    target_task_ids=target_ids,
                    hints=repair_hints,
                    context=context,
                )
                if not replan.success or replan.graph is None:
                    events.append(
                        {"event": "replan_failed", "payload": {"errors": replan.errors}}
                    )
                    return StageOutcome(
                        kind=ResultKind.FAILED,
                        message="局部重规划失败：" + ("；".join(replan.errors) if replan.errors else "未知原因"),
                        error_code="REPLAN_FAILED",
                        events=events,
                    )
                graph = replan.graph
                state = self.replanning_service.merge_state(prev_state, graph, target_ids)
                events.append(
                    {
                        "event": "replan_published",
                        "payload": {
                            "previous_version": prev_graph.version,
                            "next_version": graph.version,
                            "rerun_task_ids": target_ids,
                        },
                    }
                )
            events.append(
                {
                    "event": "plan_created",
                    "payload": {
                        "graph_id": graph.graph_id,
                        "graph_version": graph.version,
                        "task_count": len(graph.tasks),
                        "tasks": [
                            {
                                "id": task.id,
                                "goal": task.goal,
                                "dependencies": task.dependencies,
                                "capabilities": sorted(task.required_capabilities),
                            }
                            for task in graph.tasks
                        ],
                    },
                }
            )
            state = await self.runtime.run(
                graph,
                session_id,
                initial_state=state,
                event_callback=lambda event, payload: events.append(
                    {"event": _map_runtime_event(event), "payload": payload}
                ),
                budget=self.execution_budget,
                ownership=ownership,
            )
            if state.current_status == "waiting_for_user":
                events.append({"event": "graph_paused_for_human", "payload": state.metadata.get("pending_approval") or {}})
                return StageOutcome(kind=ResultKind.CLARIFICATION,
                    message="研究已完成，正在等待你确认关键候选后继续生成行程。",
                    data={"approval": state.metadata.get("pending_approval") or {}, "waiting_for_user": True},
                    events=events)
            if state.current_status == "succeeded" and revision_base_state is not None:
                self._merge_revision_evidence(revision_base_state, state, revision_researched_task_ids)
                store = getattr(self.runtime, "store", None)
                if store is not None:
                    await store.save(state, user_id=user_id, run_id=session_id)
            if state.current_status == "succeeded":
                break

            failed_ids = [
                task_id
                for task_id, task_result in state.task_results.items()
                if not task_result.success
            ]
            if state.metadata.get("budget_exceeded"):
                events.append(
                    {"event": "budget_exceeded", "payload": {"reason": state.metadata.get("budget_exceeded")}}
                )
                return StageOutcome(
                    kind=ResultKind.FAILED,
                    message=f"本次规划超出执行预算已中止（{state.metadata.get('budget_exceeded')}），失败不伪造结果。",
                    error_code="EXECUTION_BUDGET_EXCEEDED",
                    events=events,
                )
            diagnostics = self._tool_diagnostics(state)
            blocked = [d for d in diagnostics if d.get('category') in {'quota', 'authentication'}]
            if blocked:
                return StageOutcome(kind=ResultKind.FAILED,
                    message='研究所需的上游工具返回额度或授权限制；本地重规划不能修复此类问题。',
                    error_code='UPSTREAM_ACCESS_BLOCKED', data={'diagnostics': diagnostics}, events=events)
            if (
                self.replanning_service is not None
                and attempt < self.max_repair_attempts
                and failed_ids
            ):
                target_ids = self.replanning_service.compute_repair_scope(graph, failed_ids)
                repair_hints = {
                    task_id: [((state.task_results.get(task_id).error) or "failed")[:200]]
                    for task_id in failed_ids
                }
                events.append(
                    {
                        "event": "replan_triggered",
                        "payload": {"graph_version": graph.version, "failed_task_ids": failed_ids},
                    }
                )
                prev_graph, prev_state = graph, state
                attempt += 1
                continue
            events.append(
                {"event": "execution_failed", "payload": {"failed_tasks": failed_ids}}
            )
            return StageOutcome(
                kind=ResultKind.FAILED,
                message=f"任务执行失败（graph v{graph.version}），失败任务：{failed_ids or '未知'}",
                error_code="EXECUTION_FAILED",
                data={'diagnostics': diagnostics},
                events=events,
            )

        events.append(
            {
                "event": "execution_completed",
                "payload": {"graph_id": graph.graph_id, "graph_version": graph.version},
            }
        )
        if decision.route is ExecutionRoute.SIMPLE and intent.planning_intent is not True:
            terminal_outputs = "\n".join(
                str(task_result.output)
                for task_id, task_result in state.task_results.items()
                if not graph.downstream_task_ids(task_id) and task_result.output is not None
            )
            events.append({"event": "answer_ready", "payload": {"scope": "simple"}})
            return StageOutcome(
                kind=ResultKind.ANSWER,
                message=terminal_outputs or "已完成查询，但没有可展示的回答内容。",
                data={"scope": "simple", "not_an_itinerary": True},
                events=events,
            )

        # COMPLEX 成功：
        # - planning_intent=False（比较/研究类复杂问题）→ 聚合证据给出 answer，绝不强造行程
        # - planning_intent=True → 交给 Synthesizer 合成可展示、可追溯的 TripPlan（Phase D）
        if intent.planning_intent is False:
            terminal_outputs = "\n".join(
                str(task_result.output)
                for task_id, task_result in state.task_results.items()
                if not graph.downstream_task_ids(task_id) and task_result.output is not None
            )
            events.append({"event": "answer_ready", "payload": {"scope": "complex"}})
            return StageOutcome(
                kind=ResultKind.ANSWER,
                message=terminal_outputs or "已完成研究，但没有可展示的回答内容。",
                data={"scope": "complex", "not_an_itinerary": True},
                events=events,
            )

        return await self._synthesize_and_evaluate(intent, state, events, prior_revision=prior_revision, ownership=ownership)

    @staticmethod
    def _merge_revision_evidence(previous_state, revised_state, researched_task_ids: list[str]) -> None:
        """Keep prior evidence when a specialist refreshes only one research domain.

        The specialist's new result replaces its task checkpoint by default.  For
        an itinerary revision that would discard still-valid candidate POIs and
        make the synthesizer perform a single-item replacement.  Preserve both
        result sets as one evidence pool; the final Agent can then rebalance all
        days using old nearby candidates and the newly requested location.
        """
        for task_id in researched_task_ids:
            old = previous_state.task_results.get(task_id)
            new = revised_state.task_results.get(task_id)
            if old is None or new is None or not old.success or not new.success:
                continue
            old_meta, new_meta = old.metadata or {}, new.metadata or {}
            old_tools = old_meta.get("tool_results") or []
            new_tools = new_meta.get("tool_results") or []
            seen = set()
            merged_tools = []
            for item in [*old_tools, *new_tools]:
                key = (item.get("tool_name"), item.get("tool_call_id"), str(item.get("data"))[:160]) if isinstance(item, dict) else id(item)
                if key not in seen:
                    seen.add(key); merged_tools.append(item)
            new.metadata = {
                **new_meta,
                "tool_results": merged_tools,
                "sources": sorted(set((old_meta.get("sources") or []) + (new_meta.get("sources") or []))),
                "revision_evidence": {"preserved_previous_count": len(old_tools), "new_count": len(new_tools)},
            }
            new.output = f"上一版可复用研究：\n{str(old.output or '')[:1800]}\n\n本轮补充研究：\n{str(new.output or '')[:1800]}"

    async def _synthesize_and_evaluate(self, intent, state, events, repair_attempt=0, prior_revision=0, ownership=None):
        graph = state.graph
        if self.synthesizer is None:
            events.append({"event": "synthesis_pending", "payload": {}})
            return StageOutcome(
                kind=ResultKind.FAILED,
                message=(
                    f"研究任务已执行完成（graph {graph.graph_id} v{graph.version}），"
                    "但未装配行程合成器，因此不返回伪造的完整行程。"
                ),
                error_code="TRIP_PLAN_SYNTHESIS_NOT_READY",
                data={
                    "graph_id": graph.graph_id,
                    "graph_version": graph.version,
                    "completed_tasks": len(state.task_results),
                },
                events=events,
            )

        try:
            with self._llm_scope("SYNTHESIS", user_id=getattr(ownership, "user_id", None), run_id=getattr(ownership, "run_id", None), graph=graph):
                trip_plan = await self.synthesizer.synthesize(intent, state)
            trip_plan.plan_revision = prior_revision + repair_attempt + 1
        except Exception as error:
            events.append(
                {"event": "synthesis_failed", "payload": {"error": str(error)}}
            )
            return StageOutcome(
                kind=ResultKind.FAILED,
                message=f"行程合成失败（研究结果已保留，可重试合成而不重跑研究）：{type(error).__name__}: {error}",
                error_code="SYNTHESIS_FAILED",
                data={'diagnostics': [error.diagnostic]} if getattr(error, 'diagnostic', None) else None,
                events=events,
            )
        events.append(
            {
                "event": "synthesis_completed",
                "payload": {
                    "graph_version": trip_plan.graph_version,
                    "plan_revision": trip_plan.plan_revision,
                    "day_count": len(trip_plan.days),
                },
            }
        )

        # Phase E：只读质量评估（不调用工具/不改计划）
        if self.evaluator is not None:
            evaluation = await self.evaluator.evaluate(intent, trip_plan, state)
            events.append(
                {
                    "event": "evaluation_completed",
                    "payload": {
                        "status": evaluation.status.value,
                        "issue_count": len(evaluation.issues),
                    },
                }
            )
            if evaluation.status.value == "fail":
                if repair_attempt < self.max_repair_attempts:
                    from ..domain.evaluation import IssueSeverity, RepairScope
                    high = [i for i in evaluation.issues if i.severity is IssueSeverity.HIGH]
                    state.metadata['synthesis_repair_issues'] = [i.model_dump(mode='json') for i in high]
                    affected = sorted({tid for i in high if i.repair_scope is RepairScope.TASK for tid in i.affected_task_ids})
                    if affected and self.replanning_service is not None:
                        targets = self.replanning_service.compute_repair_scope(graph, affected)
                        hints = {tid: [i.message for i in high if tid in i.affected_task_ids] for tid in affected}
                        context = PlanningContext(session_id=state.session_id, intent=intent,
                                                  available_capabilities=self.capabilities)
                        repair = self.replanning_service.build_repair_graph(graph, targets, hints, context)
                        if repair.success:
                            events.append({'event': 'replan_published', 'payload': {
                                'reason': 'evaluation', 'previous_version': graph.version,
                                'next_version': repair.graph.version, 'rerun_task_ids': targets}})
                            initial = self.replanning_service.merge_state(state, repair.graph, targets)
                            state = await self.runtime.run(repair.graph, state.session_id, initial_state=initial,
                                budget=self.execution_budget, ownership=ownership,
                                event_callback=lambda e,p: events.append({'event': _map_runtime_event(e), 'payload': p}))
                            if state.current_status != 'succeeded':
                                return StageOutcome(kind=ResultKind.FAILED, error_code='REPAIR_EXECUTION_FAILED',
                                    message='评估后的研究修复失败，未将不合格行程当成成功。',
                                    data={'diagnostics': self._tool_diagnostics(state)}, events=events)
                        else:
                            return StageOutcome(kind=ResultKind.FAILED, error_code='REPLAN_FAILED',
                                                message='；'.join(repair.errors), events=events)
                    events.append({'event': 'synthesis_repair_started', 'payload': {'issues': [i.code for i in high]}})
                    return await self._synthesize_and_evaluate(intent, state, events, repair_attempt + 1, prior_revision, ownership=ownership)
                return StageOutcome(
                    kind=ResultKind.FAILED,
                    message=(
                        f"行程评估未通过（{len(evaluation.issues)} 个问题），请修正后重新生成。"
                    ),
                    error_code="EVALUATION_FAILED",
                    data={
                        "plan_revision": trip_plan.plan_revision,
                        "issues": [
                            {
                                "code": issue.code,
                                "severity": issue.severity.value,
                                "message": issue.message,
                            }
                            for issue in evaluation.issues
                        ],
                    },
                    events=events,
                )
        data = trip_plan.model_dump(mode="json")
        data['diagnostics'] = self._tool_diagnostics(state)
        if self.evaluator is not None:
            data["evaluation"] = {
                "status": evaluation.status.value,
                "issues": [issue.code for issue in evaluation.issues],
                'details': [issue.model_dump(mode='json') for issue in evaluation.issues],
            }
        return StageOutcome(
            kind=ResultKind.TRIP_PLAN,
            message=(
                f"已根据研究证据生成 {trip_plan.city} 的行程"
                f"（graph v{trip_plan.graph_version}，共 {len(trip_plan.days)} 天）。"
                f"共 {len(trip_plan.assumptions)} 条假设已标注。"
            ),
            data=data,
            events=events,
        )

    @staticmethod
    def _tool_diagnostics(state):
        diagnostics = []
        seen = set()
        for tid, task in state.task_results.items():
            for tool in task.metadata.get('tool_results', []):
                if tool.get('success'):
                    continue
                diagnostic = tool.get('metadata', {}).get('diagnostic') or {
                    'layer': 'tool', 'category': 'unknown', 'attribution': 'undetermined', 'message': tool.get('error')}
                item = {'task_id': tid, 'tool': tool.get('tool_name'), **diagnostic}
                key = (tid, item['tool'], item.get('message'))
                if key not in seen:
                    seen.add(key)
                    diagnostics.append(item)
        return diagnostics


def _map_runtime_event(event: str) -> str:
    if event == "task_succeeded":
        return "task_completed"
    if event == "task_failed":
        return "task_completed"
    return event
