"""Phase B：bootstrap 生产装配（真实 M1→M5，无 Fake）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.application import TravelApplicationService, TravelPipelineProcessor
from app.application.travel_service import ResultKind, RunStatus, StageOutcome
from app.bootstrap import DEFAULT_CAPABILITIES, BootstrapError, build_travel_app
from app.config import Settings
from app.persistence import SQLiteRunRepository
from tests.fakes import FakeMessageProcessor


def _settings(workdir: Path, **overrides) -> Settings:
    base = dict(
        llm_api_key="test-key",
        llm_base_url="https://llm.invalid/v1",
        llm_model="test-model",
        db_path=str(workdir / "runs.sqlite"),
        auth_secret_key="bootstrap-test-secret-long-enough",
    )
    base.update(overrides)
    return Settings(**base)


def test_build_raises_when_llm_config_missing(workdir):
    settings = _settings(workdir, llm_api_key=None, llm_base_url=None, llm_model=None)
    with pytest.raises(BootstrapError, match="生产配置缺失"):
        build_travel_app(settings)


def test_build_defaults_to_real_pipeline(workdir):
    settings = _settings(workdir)
    app_service = build_travel_app(settings)
    try:
        assert isinstance(app_service, TravelApplicationService)
        assert app_service.processor is not None
        assert isinstance(app_service.processor, TravelPipelineProcessor)
        assert app_service.processor.capabilities == DEFAULT_CAPABILITIES
        assert (workdir / "runs.sqlite").exists()
    finally:
        app_service.repository.close()


def test_default_amap_mode_excludes_transit():
    """默认不让 Agent 为公交核验消耗长时间的 MCP 调用。"""
    assert Settings().amap_tool_mode == "no_transit"


def test_build_accepts_injected_fake_processor(workdir):
    settings = _settings(workdir)
    fake = FakeMessageProcessor(
        StageOutcome(kind=ResultKind.ANSWER, message="fake answer")
    )
    app_service = build_travel_app(settings, processor=fake)
    try:
        assert app_service.processor is fake
    finally:
        app_service.repository.close()


async def test_service_without_processor_returns_typed_failure(workdir):
    """裸 TravelApplicationService（无处理器）也必须类型化失败，绝不假成功。"""
    repo = SQLiteRunRepository(workdir / "bare.sqlite")
    app_service = TravelApplicationService(repo)
    try:
        record = await app_service.run_single_plan("我想去北京玩三天")
        assert record.status is RunStatus.FAILED
        assert record.kind is ResultKind.FAILED
        assert record.error_code == "INTENT_ENGINE_NOT_READY"
        assert record.data is None
    finally:
        await app_service.close()


async def test_bare_service_with_fake_processor_returns_kind(workdir):
    repo = SQLiteRunRepository(workdir / "withproc.sqlite")
    outcome = StageOutcome(
        kind=ResultKind.TRIP_PLAN,
        message="ok",
        data={"city": "Beijing"},
        events=[{"event": "answer_ready", "payload": {}}],
    )
    app_service = TravelApplicationService(repo, processor=FakeMessageProcessor(outcome))
    try:
        record = await app_service.run_single_plan("请规划北京三天")
        assert record.status is RunStatus.COMPLETED
        assert record.kind is ResultKind.TRIP_PLAN
        assert record.data == {"city": "Beijing"}
        events = await repo.list_events(record.run_id)
        names = [event.event for event in events]
        assert names == ["run_created", "message_received", "answer_ready", "stage_completed"]
    finally:
        await app_service.close()
