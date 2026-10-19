

def test_collect_coordinate_pool_picks_location_from_structured_data():
    """Phase G 修复：tool 返回的 data 是结构化 dict（如 {"location":"lng,lat"}）时也应被坐标池收录。"""
    from app.domain.provenance import EvidenceEntry, collect_coordinate_pool

    index = {
        "t@1#tool0": EvidenceEntry(
            evidence_id="t@1#tool0",
            task_id="t",
            graph_version=1,
            tool_name="maps_search_detail",
            data={"location": "116.397029,39.917839", "name": "故宫博物院"},
            snippet="",  # 文本里没写坐标也能收录
        ),
        "t@1#tool1": EvidenceEntry(
            evidence_id="t@1#tool1",
            task_id="t",
            graph_version=1,
            tool_name="maps_search_detail",
            data=[{"location": "100.176,25.690"}, {"name": "大理古城"}],
            snippet="",
        ),
    }
    pool = collect_coordinate_pool(index)
    assert "116.397029,39.917839" in pool
    assert "100.176000,25.690000" in pool
    assert pool["116.397029,39.917839"]["tool_name"] == "maps_search_detail"


def test_collect_coordinate_pool_ignores_out_of_range():
    from app.domain.provenance import EvidenceEntry, collect_coordinate_pool

    index = {
        "x@1#tool0": EvidenceEntry(
            evidence_id="x@1#tool0",
            task_id="x",
            graph_version=1,
            tool_name="t",
            data={"location": "999.0,888.0"},  # 非法经纬度
            snippet="",
        ),
    }
    pool = collect_coordinate_pool(index)
    assert pool == {}
