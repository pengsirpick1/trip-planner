"""Explicit opt-in online smoke test. It is never imported by run_all."""

from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path

from app.application.travel_service import RunRecord
from app.bootstrap import _build_pipeline_parts
from app.config import Settings, validate_core_config


CASE_PATH = Path(__file__).resolve().parents[1] / "cases" / "online_cases.json"


async def main(max_cases: int) -> None:
    if not 1 <= max_cases <= 10:
        raise SystemExit("--max-cases must be between 1 and 10")
    settings = Settings()
    missing = validate_core_config(settings)
    if missing:
        raise SystemExit("online smoke requires production configuration: " + "; ".join(missing))
    cases = json.loads(CASE_PATH.read_text(encoding="utf-8"))[:max_cases]
    print("ONLINE SMOKE — real API usage may incur cost")
    print(f"provider base URL: {settings.llm_base_url}")
    print(f"model: {settings.llm_model}")
    print(f"case count: {len(cases)}; max ReAct steps: 6; max tool calls: 9; retry limit: 3")
    parts = _build_pipeline_parts(settings, capabilities=None, llm=None)
    for index, case in enumerate(cases, start=1):
        run = RunRecord(run_id=f"online-{uuid.uuid4().hex[:12]}", session_id=f"online-smoke-{index}")
        outcome = await parts.processor.process(case["message"], run)
        usage = getattr(parts.llm, "usage", {}) or {}
        print(json.dumps({"case_id": case["case_id"], "kind": outcome.kind.value, "success": outcome.error_code is None, "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens"), "total_tokens": usage.get("total_tokens")}, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a small, explicit online Agent Runtime smoke test")
    parser.add_argument("--max-cases", type=int, default=5)
    args = parser.parse_args()
    asyncio.run(main(args.max_cases))

