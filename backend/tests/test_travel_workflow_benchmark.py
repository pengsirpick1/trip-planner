from benchmarks.graph_runtime.bench_travel_workflow_comparison import RESEARCH_TASK_IDS, run_benchmark, travel_workflow_graph


async def test_travel_workflow_benchmark_compares_equal_work_with_real_dependencies():
    graph = travel_workflow_graph()
    graph.validate_graph()
    assert graph.task("spatial_route_validation").dependencies == [
        "must_have_poi_research", "nearby_poi_research", "hotel_research"
    ]
    assert graph.task("itinerary_synthesis").dependencies == list(RESEARCH_TASK_IDS)

    records = await run_benchmark(samples=1)
    assert {record.mode for record in records} == {"dag_parallel", "dag_serial", "single_agent_serial"}
    assert all(record.success and record.tool_calls == len(RESEARCH_TASK_IDS) for record in records)
    assert all(record.metadata["same_final_artifact"] for record in records)
