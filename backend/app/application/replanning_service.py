"""局部重规划服务（Phase F）。

设计要点：
- 确定性增量：vN+1 = vN 全量结构克隆（同 id/依赖），只对"目标任务"追加修复提示；
  builder 负责校验并打上新版本号 → 不需要第二次 Planner LLM 调用，便宜且可复现。
- merge_state：把 vN 中不在重跑集里的成功任务结果带进 vN+1 初始态（并给旧结果打上
  graph_version 标签供溯源），重跑集全部置 PENDING → runtime 只执行修复任务。
- 服务本身不执行任何工具；只管算"重跑谁、新图长什么样、状态怎么并"。
"""

from __future__ import annotations

from ..domain import GraphExecutionState, PlanningContext, PlanningResult, TaskGraph, TaskStatus
from ..domain.models import TaskResult
from ..domain.replanning import ReplanningOutcome
from ..planning.builder import PlanBuilder


class ReplanningService:
    def __init__(self, builder: PlanBuilder | None = None):
        self.builder = builder or PlanBuilder()

    # ---- 范围决策 ----

    def compute_repair_scope(
        self, prev_graph: TaskGraph, affected_task_ids: list[str]
    ) -> list[str]:
        """受影响任务 ∪ 其下游（含传递）。"""
        scope = set(affected_task_ids)
        for task_id in affected_task_ids:
            scope |= prev_graph.downstream_task_ids(task_id)
        order = [t.id for t in prev_graph.tasks if t.id in scope]
        return order

    # ---- 图发布 ----

    def build_repair_graph(
        self,
        prev_graph: TaskGraph,
        target_task_ids: list[str],
        hints: dict[str, list[str]],
        context: PlanningContext,
    ) -> ReplanningOutcome:
        next_version = prev_graph.version + 1
        targets = set(target_task_ids)
        draft = self._clone_draft(prev_graph, targets, hints)
        result: PlanningResult = self.builder.build(draft, context, version=next_version)
        if not result.success or result.graph is None:
            return ReplanningOutcome(success=False, errors=result.errors)
        return ReplanningOutcome(success=True, graph=result.graph)

    @staticmethod
    def _clone_draft(prev_graph: TaskGraph, targets: set[str], hints: dict[str, list[str]]):
        from ..domain.planning import PlanDraft, PlanTaskDraft

        tasks = []
        for task in prev_graph.tasks:
            goal = task.goal
            if task.id in targets and hints.get(task.id):
                hint_text = "；".join(hints[task.id][:3])
                goal = f"{goal}\n（上一版本该任务未达要求，请重点修正：{hint_text}）"
            tasks.append(
                PlanTaskDraft(
                    id=task.id,
                    goal=goal,
                    task_type=task.task_type,
                    dependencies=list(task.dependencies),
                    required_capabilities=set(task.required_capabilities),
                    expected_output=task.expected_output or task.goal,
                    completion_criteria=task.completion_criteria or "完成并说明证据",
                    priority=task.priority,
                )
            )
        return PlanDraft(tasks=tasks)

    # ---- 状态合并 ----

    def merge_state(
        self,
        prev_state: GraphExecutionState,
        next_graph: TaskGraph,
        rerun_task_ids: list[str],
    ) -> GraphExecutionState:
        """vN 成功且不在重跑集的任务 → SUCCEEDED+保留结果（打版本标签）；其余 PENDING。"""
        rerun = set(rerun_task_ids)
        old_version = prev_state.graph.version
        task_results = {}
        task_attempts = {}
        statuses = {}
        for task_id in next_graph.tasks:
            tid = task_id.id
            kept = (
                tid not in rerun
                and prev_state.task_statuses.get(tid) is TaskStatus.SUCCEEDED
                and tid in prev_state.task_results
            )
            if kept:
                kept_result = prev_state.task_results[tid].model_copy(deep=True)
                kept_result.metadata["graph_version"] = old_version
                task_results[tid] = kept_result
                statuses[tid] = TaskStatus.SUCCEEDED
                attempts = prev_state.task_attempts.get(tid)
                if attempts:
                    task_attempts[tid] = [a.model_copy(deep=True) for a in attempts]
            else:
                statuses[tid] = TaskStatus.PENDING
        metadata = dict(prev_state.metadata)
        replans = list(metadata.get("replans", []))
        replans.append(
            {
                "previous_version": old_version,
                "next_version": next_graph.version,
                "rerun_task_ids": sorted(rerun),
                "kept_task_ids": sorted(
                    tid for tid, status in statuses.items() if status is TaskStatus.SUCCEEDED
                ),
            }
        )
        metadata["replans"] = replans
        return GraphExecutionState(
            session_id=prev_state.session_id,
            graph=next_graph.model_copy(deep=True),
            task_statuses=statuses,
            task_results=task_results,
            task_attempts=task_attempts,
            current_status="pending",
            metadata=metadata,
        )
