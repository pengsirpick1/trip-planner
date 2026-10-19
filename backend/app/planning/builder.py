"""声明式 DAG 构建（迁自 travel_agent/agent/planning/builder.py + Phase B 版本参数）。

Phase B 修复（审计 G8）：builder 支持显式 version；graph_id 形如 {session}-v{version}，
为 Phase F 局部重规划发布 vN+1 提供前置。默认仍是 v1。
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain import ConditionalEdge, ExecutionProfile, PlanDraft, PlanningContext, PlanningResult, Task, TaskGraph


def _default_agent(capabilities: set[str]) -> str:
    """Deterministic fallback keeps older Planner outputs compatible."""
    if "accommodation_research" in capabilities:
        return "hotel_agent"
    if "place_research" in capabilities:
        return "poi_agent"
    if "geospatial_reasoning" in capabilities or "transport_research" in capabilities:
        return "transport_agent"
    if "weather_research" in capabilities:
        return "weather_agent"
    if "budget_policy" in capabilities:
        return "budget_policy"
    if "itinerary_synthesis" in capabilities:
        return "itinerary_agent"
    return "research_agent"


class PlanBuilder:
    def __init__(self, max_tasks: int = 12):
        self.max_tasks = max_tasks

    def build(
        self,
        draft: PlanDraft,
        context: PlanningContext,
        version: int = 1,
    ) -> PlanningResult:
        if version < 1:
            return PlanningResult(success=False, errors=[f"invalid graph version: {version}"])
        errors = []
        if len(draft.tasks) > self.max_tasks:
            errors.append(f"task count exceeds limit ({self.max_tasks})")
        unsupported = sorted(
            {
                cap
                for task in draft.tasks
                for cap in task.required_capabilities
                if cap not in context.available_capabilities
            }
        )
        if unsupported:
            errors.append(f"unsupported capabilities: {', '.join(unsupported)}")
        if errors:
            return PlanningResult(success=False, errors=errors)

        tasks = [
            Task(
                id=task.id.strip(), goal=task.goal, task_type=task.task_type,
                dependencies=([] if "accommodation_research" in task.required_capabilities else task.dependencies),
                required_capabilities=task.required_capabilities, expected_output=task.expected_output,
                completion_criteria=task.completion_criteria, priority=task.priority,
                assigned_agent=task.assigned_agent or _default_agent(task.required_capabilities),
                local_context={"capabilities": sorted(task.required_capabilities)},
            )
            for task in draft.tasks
        ]
        profile_metadata = self._compile_parallel_graph(tasks, context)
        requires_approval = bool(draft.requires_human_approval or context.metadata.get("requires_human_approval"))
        edges: list[ConditionalEdge] = []
        if requires_approval:
            synthesis = next((task for task in tasks if "itinerary_synthesis" in task.required_capabilities), None)
            if synthesis is not None:
                approval_id = "human_approval"
                if any(task.id == approval_id for task in tasks):
                    errors.append("reserved task id human_approval already used")
                else:
                    approval = Task(id=approval_id, goal="等待用户确认关键候选后继续生成行程",
                                    task_type="human_approval", dependencies=list(synthesis.dependencies),
                                    required_capabilities={"human_approval"}, expected_output="user decision",
                                    completion_criteria="approved or revise", priority=synthesis.priority,
                                    assigned_agent="human", local_context={"decision_scope": "itinerary_candidates"})
                    synthesis.dependencies = [approval_id]
                    tasks.append(approval)
                    edges = [ConditionalEdge(source_task_id=approval_id, on="approved", target_task_id=synthesis.id),
                             ConditionalEdge(source_task_id=approval_id, on="revise", target_task_id=None)]
        graph = TaskGraph(
            graph_id=f"{context.session_id}-v{version}",
            version=version,
            tasks=tasks,
            metadata={"rationale_summary": draft.rationale_summary, **draft.metadata, **profile_metadata},
            conditional_edges=edges,
        )
        try:
            graph.validate_graph()
        except ValueError as error:
            return PlanningResult(success=False, errors=[str(error)])
        if graph.tasks and not any(
            not graph.downstream_task_ids(task.id) for task in graph.tasks
        ):
            return PlanningResult(success=False, errors=["graph has no terminal task"])
        return PlanningResult(
            success=True,
            graph=graph,
            metadata={
                "task_count": len(graph.tasks),
                "dependency_count": sum(len(t.dependencies) for t in graph.tasks),
                "version": version,
                "created_at": datetime.now(UTC).isoformat(),
            },
        )

    @staticmethod
    def _needs_accommodation(context: PlanningContext) -> bool:
        """酒店是条件节点：多日游或用户明确提出住宿时才进入图。"""
        intent = context.intent
        if (intent.duration_days or 0) >= 2:
            return True
        terms = ("酒店", "住宿", "住哪", "住在", "民宿", "青旅")
        return any(term in intent.raw_query for term in terms)

    def _compile_parallel_graph(self, tasks: list[Task], context: PlanningContext) -> dict:
        """将 LLM 的研究意图编译为受控的并行 DAG。

        LLM 可以说明需要哪些研究，但不拥有任意依赖边的决定权。依赖由数据产物决定：
        POI/酒店候选 -> 空间校验 -> 合成；预算策略是本地确定性节点。
        未带 execution_profile 的旧调用保持原 PlanDraft 依赖，兼容历史测试和重放。
        """
        raw_profile = context.metadata.get("execution_profile")
        if not raw_profile:
            return {}
        try:
            profile = ExecutionProfile(raw_profile)
        except ValueError:
            return {"graph_compiler_warning": f"unknown execution_profile: {raw_profile}"}
        if profile is ExecutionProfile.DIRECT:
            return {"execution_profile": profile.value, "max_research_agents": 0,
                    "research_task_budget": 0, "worker_concurrency_budget": profile.max_concurrent_workers}

        synthesis = [task for task in tasks if "itinerary_synthesis" in task.required_capabilities]
        research = [task for task in tasks if task not in synthesis]
        kept: list[Task] = []
        pruned: list[str] = []
        public_transit_supported = bool(context.metadata.get("public_transit_supported", False))
        needs_accommodation = self._needs_accommodation(context)
        has_budget_research = False
        for task in research:
            caps = task.required_capabilities
            label = f"{task.id} {task.goal}".lower()
            if "accommodation_research" in caps and not needs_accommodation:
                pruned.append(task.id)
                continue
            # 当前产品不产出公交/地铁详情；步行/驾车空间校验仍使用 geospatial_reasoning。
            if "transport_research" in caps and not public_transit_supported and any(
                word in label for word in ("subway", "transit", "metro", "公交", "地铁")
            ):
                pruned.append(task.id)
                continue
            if "budget" in label or task.task_type == "budget_policy":
                has_budget_research = True
                pruned.append(task.id)
                continue
            kept.append(task)

        # LIGHT 档位的“必去点 + 周边”是两个可独立检索的 Evidence 流。
        # 不依赖 LLM 是否恰好把它们拆成两个 task，避免一个泛化地点任务吞掉硬约束。
        nearby_terms = ("附近", "周边", "步行", "邻近", "nearby", "walkable")
        place_tasks = [task for task in kept if "place_research" in task.required_capabilities]
        if (
            profile is ExecutionProfile.LIGHT
            and context.intent.must_have
            and len(place_tasks) == 1
            and any(term in context.intent.raw_query.lower() for term in nearby_terms)
        ):
            original = place_tasks[0]
            kept.remove(original)
            pruned.append(original.id)
            names = "、".join(context.intent.must_have[:4])
            kept.extend([
                Task(
                    id="must_have_poi_research", goal=f"核验用户必须安排的地点：{names}；收集开放、入口、游览时长与坐标证据",
                    task_type="must_have_poi_research", dependencies=[], required_capabilities={"place_research"},
                    expected_output="必去地点的可核验游览约束", completion_criteria="每个必去地点有事实证据或明确未知项",
                    priority=-2, assigned_agent="poi_agent", local_context={"must_have": context.intent.must_have, "capabilities": ["place_research"]},
                ),
                Task(
                    id="nearby_poi_research", goal=f"围绕 {names} 寻找用户数量要求内、步行可达的相邻景点；给出距离、时长与取舍理由",
                    task_type="nearby_poi_research", dependencies=[], required_capabilities={"place_research", "geospatial_reasoning"},
                    expected_output="相邻景点候选与步行可行性证据", completion_criteria="候选数量满足要求，无法步行的候选被排除",
                    priority=-1, assigned_agent="poi_agent", local_context={"anchor_places": context.intent.must_have, "capabilities": ["place_research", "geospatial_reasoning"]},
                ),
            ])

        # 预算是约束传播，不是新的外部研究流；有预算时以零 LLM/零工具节点表达。
        if (has_budget_research or context.intent.budget is not None) and "budget_policy" in context.available_capabilities:
            kept.append(Task(
                id="budget_policy", goal="将用户预算约束转为可验证费用与未知费用规则",
                task_type="budget_policy", dependencies=[], required_capabilities={"budget_policy"},
                expected_output="每日电费上限与未知费用处理规则", completion_criteria="不编造价格或库存",
                priority=0, assigned_agent="budget_policy",
                local_context={"budget": context.intent.budget, "duration_days": context.intent.duration_days,
                               "constraints": context.intent.constraints, "capabilities": ["budget_policy"]},
            ))

        # 控制并行工作者数量。确定性节点不计入 Agent 数；优先保留地点、住宿、天气和空间任务。
        deterministic = [task for task in kept if "budget_policy" in task.required_capabilities]
        workers = [task for task in kept if task not in deterministic]
        rank = lambda task: (
            0 if "place_research" in task.required_capabilities else
            1 if "accommodation_research" in task.required_capabilities else
            2 if "weather_research" in task.required_capabilities else 3,
            task.priority, task.id,
        )
        selected_workers = sorted(workers, key=rank)[:profile.max_research_agents]
        pruned.extend(task.id for task in workers if task not in selected_workers)
        kept = [*selected_workers, *deterministic]

        poi_ids = [task.id for task in kept if "place_research" in task.required_capabilities]
        hotel_ids = [task.id for task in kept if "accommodation_research" in task.required_capabilities]
        for task in kept:
            caps = task.required_capabilities
            is_spatial_validation = task.task_type in {"spatial_route_validation", "route_validation"} or (
                "geospatial_reasoning" in caps and "place_research" not in caps and "accommodation_research" not in caps
            )
            # 只有空间校验消费上游候选；其他研究流必须保持依赖为空。
            task.dependencies = [*poi_ids, *hotel_ids] if is_spatial_validation else []
            if task.id in task.dependencies:
                task.dependencies.remove(task.id)

        # Synthesis 是唯一 fan-in 节点，依赖所有本轮真正启用的 Evidence 产物。
        for task in synthesis:
            task.dependencies = [item.id for item in kept]
        tasks[:] = [*kept, *synthesis]
        return {
            "execution_profile": profile.value,
            # max_research_agents is a legacy trace field.  It has always
            # limited research Task branches, never Python Agent instances.
            "max_research_agents": profile.max_research_agents,
            "research_task_budget": profile.max_research_agents,
            "worker_concurrency_budget": profile.max_concurrent_workers,
            "graph_compiler": "artifact_dependency_v1",
            "public_transit_supported": public_transit_supported,
            "pruned_task_ids": pruned,
        }
