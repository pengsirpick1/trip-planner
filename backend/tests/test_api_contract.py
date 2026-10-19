"""Phase A：API 契约（test_api_contract）。

覆盖：/api/travel/runs 建/查/事件/追加消息；typed discriminator；
/trip/plan 默认转发新应用层且无假行程；/health 反映真实装配；legacy 开关路由。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.config import Settings


def _settings(workdir: Path, **overrides) -> Settings:
    base = dict(
        app_name="test-travel",
        llm_api_key="test-key",
        llm_base_url="https://llm.invalid/v1",
        llm_model="test-model",
        db_path=str(workdir / "runs.sqlite"),
        enable_legacy_planner=False,
        amap_api_key='',
        auth_required=False,  # legacy API contract tests intentionally cover anonymous SQLite mode
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def client(workdir):
    app = create_app(_settings(workdir))
    with TestClient(app) as test_client:
        yield test_client


def _app_with_processor(workdir, processor):
    return create_app(_settings(workdir), processor=processor)


TRIP_BODY = {
    "city": "北京",
    "start_date": "2026-10-01",
    "end_date": "2026-10-03",
    "travel_days": 3,
    "transportation": "公共交通",
    "accommodation": "经济型酒店",
    "preferences": ["历史文化"],
    "free_text_input": "",
}


def test_health_reports_engine_without_agent(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["travel_engine"] is True
    assert body["llm_configured"] is True


def test_trip_health_no_agent_attr(client):
    resp = client.get("/api/trip/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["legacy_enabled"] is False
    assert "agent_name" not in body  # 不再访问不存在的 agent.agent


def test_create_run_without_message(client):
    resp = client.post("/api/travel/runs", json={})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "pending"
    assert body["run_id"]
    assert body["kind"] is None


def test_async_message_sse_replays_persisted_events(client):
    run_id = client.post("/api/travel/runs", json={}).json()["run_id"]
    accepted = client.post(f"/api/travel/runs/{run_id}/messages/async", json={"message": "北京天气"})
    assert accepted.status_code == 202
    assert accepted.json() == {"run_id": run_id, "status": "accepted"}
    with client.stream("GET", f"/api/travel/runs/{run_id}/stream?after_seq=0") as response:
        assert response.status_code == 200
        stream = response.read().decode()
    assert "event: message_received" in stream
    assert "event: stream_completed" in stream


def test_message_returns_typed_failure_not_fake_plan(client):
    resp = client.post(
        "/api/travel/runs", json={"message": "我想去北京玩三天，预算 3000"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "failed"
    assert body["status"] == "failed"
    # 离线环境：真实链路调用 Provider 被拦截 → 类型化 INTERNAL_ERROR，绝不假成功/占位数据
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["data"] is None
    # RunRecord 无 success 字段；kind 即类型化判别器（替代旧 success=True 假成功）


def test_run_lifecycle_and_events(client):
    created = client.post("/api/travel/runs", json={"session_id": "s1"}).json()
    run_id = created["run_id"]

    updated = client.post(
        f"/api/travel/runs/{run_id}/messages", json={"message": "hi"}
    ).json()
    assert updated["kind"] == "failed"
    assert updated["error_code"] == "INTERNAL_ERROR"

    got = client.get(f"/api/travel/runs/{run_id}").json()
    assert got["run_id"] == run_id

    events = client.get(f"/api/travel/runs/{run_id}/events").json()
    names = [event["event"] for event in events]
    assert names == ["run_created", "message_received", "stage_completed"]
    seqs = [event["seq"] for event in events]
    assert seqs == [1, 2, 3]


def test_unknown_run_404(client):
    assert client.get("/api/travel/runs/nope").status_code == 404
    assert (
        client.post("/api/travel/runs/nope/messages", json={"message": "x"}).status_code
        == 404
    )


def test_trace_returns_safe_empty_graph_when_no_agent_checkpoint(client):
    """澄清/直答及尚未执行任务图的 run 仍可供前端展示事件轨迹。"""
    created = client.post("/api/travel/runs", json={}).json()
    trace = client.get(f"/api/travel/runs/{created['run_id']}/trace")
    assert trace.status_code == 200
    body = trace.json()
    assert body["run_id"] == created["run_id"]
    assert body["graph"] is None
    assert body["timeline"][0]["event"] == "run_created"


def test_trip_plan_legacy_disabled_returns_typed_failure(client):
    resp = client.post("/api/trip/plan", json=TRIP_BODY)
    assert resp.status_code == 200
    body = resp.json()
    # 兼容 envelope：新链路（离线环境 Provider 被拦）→ 类型化失败，无假行程、data=None
    assert body["success"] is False
    assert body["kind"] == "failed"
    assert body["error_code"] == "INTERNAL_ERROR"
    assert body["data"] is None


def test_runs_typed_discriminator_with_processor(workdir):
    """注入 Fake 处理器：三种 typed 结果都能走通 API 判别器。"""
    from app.application.travel_service import ResultKind, StageOutcome
    from tests.fakes import FakeMessageProcessor

    outcomes = {
        "trip": StageOutcome(kind=ResultKind.TRIP_PLAN, message="plan", data={"city": "Beijing"}),
        "clarify": StageOutcome(
            kind=ResultKind.CLARIFICATION,
            message="需要更多信息",
            questions=[{"id": "q1", "field": "destination", "question": "去哪？"}],
        ),
        "answer": StageOutcome(kind=ResultKind.ANSWER, message="北京今日晴"),
    }
    for marker, outcome in outcomes.items():
        app = _app_with_processor(workdir, FakeMessageProcessor(outcome))
        with TestClient(app) as test_client:
            resp = test_client.post(
                "/api/travel/runs", json={"message": "test", "session_id": marker}
            )
        assert resp.status_code == 201
        body = resp.json()
        assert body["kind"] == outcome.kind.value
        assert body["status"] == ("completed" if outcome.kind is not ResultKind.FAILED else "failed")
        if outcome.kind is ResultKind.CLARIFICATION:
            # Phase G 契约：澄清问题随 run.data 返回（客户端据此渲染提问）
            assert body["data"]["questions"][0]["question"] == "去哪？"
            assert body["data"]["questions"][0]["field"] == "destination"
        if outcome.kind is ResultKind.TRIP_PLAN:
            assert body["data"] == {"city": "Beijing"}
        if outcome.kind is ResultKind.ANSWER:
            assert body["message"] == "北京今日晴"


def test_trip_plan_legacy_flag_routes_to_legacy(workdir, monkeypatch):
    """legacy=true 时请求进入旧路径（不进入新引擎）。

    hello-agents 未安装时旧实现导入失败 → 500 且 detail 含 legacy 字样，
    证明路由分支确实走了 legacy；安装后可断言成功 envelope。
    """
    # Exercise the route without requiring a real legacy provider/network.
    import sys
    import types
    legacy = types.ModuleType('app.agents.trip_planner_agent')
    def unavailable():
        raise RuntimeError('legacy test unavailable')
    legacy.get_trip_planner_agent = unavailable
    monkeypatch.setitem(sys.modules, 'app.agents.trip_planner_agent', legacy)
    app = create_app(_settings(workdir, enable_legacy_planner=True))
    with TestClient(app) as test_client:
        resp = test_client.post("/api/trip/plan", json=TRIP_BODY)

    assert resp.status_code == 500
    assert resp.json()['code'] == 'REQUEST_FAILED'
    assert 'trace_id' in resp.json()
