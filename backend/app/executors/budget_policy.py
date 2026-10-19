"""确定性预算约束节点：不为格式整理额外消耗 LLM 或地图调用。"""

from __future__ import annotations

from datetime import UTC, datetime

from ..domain import TaskResult


class BudgetPolicyExecutor:
    executor_id = "budget-policy"

    async def execute(self, task, context) -> TaskResult:
        local = task.local_context or {}
        budget = local.get("budget")
        duration = local.get("duration_days") or 1
        constraints = local.get("constraints") or {}
        per_day = round(float(budget) / max(1, int(duration)), 2) if budget is not None else None
        output = (
            f"用户总预算为 {budget} 元，按 {duration} 天折算约 {per_day} 元/天；"
            "仅将已验证价格计入，未验证门票、餐饮和交通价格标记为未知。"
            if budget is not None else "未提供明确预算；不推断价格或库存。"
        )
        return TaskResult(
            task_id=task.id, success=True, output=output, executor_id=self.executor_id,
            started_at=datetime.now(UTC), finished_at=datetime.now(UTC),
            metadata={"tool_calls": 0, "sources": [], "tool_results": [], "llm_steps": [],
                      "budget_policy": {"total_budget": budget, "per_day_budget": per_day,
                                        "duration_days": duration, "constraints": constraints}},
        )
