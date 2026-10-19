from app.domain import Task, TaskResult
from app.executors import EvidenceHandoffExecutor


async def test_evidence_handoff_is_deterministic_and_compact():
    class Context:
        dependency_results = {
            "places": TaskResult(
                task_id="places",
                success=True,
                output="x" * 1000,
                metadata={
                    "sources": ["mcp://amap/search", "mcp://amap/search"],
                    "tool_results": [{"tool_name": "maps_text_search"}] * 3,
                },
            )
        }

    executor = EvidenceHandoffExecutor()
    result = await executor.execute(
        Task(id="itinerary_synthesis", goal="handoff", required_capabilities={"itinerary_synthesis"}),
        Context(),
    )

    assert result.success is True
    assert result.executor_id == "evidence-handoff"
    assert result.metadata["tool_calls"] == 0
    assert result.metadata["evidence_handoff"]["llm_calls"] == 0
    dependency = result.metadata["evidence_handoff"]["dependencies"][0]
    assert len(dependency["output_preview"]) == 240
    assert dependency["sources"] == ["mcp://amap/search"]
    assert dependency["evidence_count"] == 3
