from __future__ import annotations

import csv
import json
import platform
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


@dataclass
class BenchmarkRecord:
    benchmark: str
    case_id: str
    mode: str
    sample_id: int
    success: bool
    elapsed_ms: float
    node_executions: int | None = None
    tool_calls: int | None = None
    llm_calls: int | None = None
    prompt_tokens: int | None = 0
    completion_tokens: int | None = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def environment() -> dict[str, Any]:
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except Exception:
        commit = None
    return {
        "timestamp": datetime.now(UTC).isoformat(),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "seed": 20260911,
        "offline": True,
        "external_llm_calls": 0,
        "external_tool_api_calls": 0,
        "git_commit": commit,
        "label": "OFFLINE SYNTHETIC TOOL BENCHMARK",
    }


def write_artifacts(records: list[BenchmarkRecord], sections: list[str]) -> Path:
    root = Path(__file__).resolve().parents[2] / "benchmark_results"
    output = root / datetime.now().strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True, exist_ok=False)
    payload = {"environment": environment(), "records": [record.to_dict() for record in records]}
    (output / "results.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    with (output / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        fields = ["benchmark", "case_id", "mode", "sample_id", "success", "elapsed_ms", "node_executions", "tool_calls", "llm_calls", "prompt_tokens", "completion_tokens", "metadata"]
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for record in records:
            row = record.to_dict()
            row["metadata"] = json.dumps(row["metadata"], ensure_ascii=False, sort_keys=True)
            writer.writerow(row)
    report = ["# Agent Runtime Benchmark Report", "", "## Environment"]
    report.extend(f"- {key}: {value}" for key, value in environment().items())
    report.extend(["", *sections])
    (output / "report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    return output

