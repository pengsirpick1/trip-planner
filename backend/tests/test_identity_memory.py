from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.application.travel_service import ResultKind, StageOutcome
from app.config import Settings
from app.identity.memory import MemoryService
from app.persistence.user_repository import SQLiteUserRepository
from tests.fakes import FakeMessageProcessor


def _settings(workdir, **overrides):
    values = {
        "llm_api_key": "test-key", "llm_base_url": "https://llm.invalid/v1", "llm_model": "test-model",
        "db_path": str(workdir / "identity.sqlite"), "amap_api_key": "", "auth_required": True,
        "auth_secret_key": "a-test-secret-that-is-longer-than-twenty-four-characters",
    }
    values.update(overrides)
    return Settings(**values)


def _register(client: TestClient, username: str) -> dict:
    response = client.post("/api/auth/register", json={"username": username, "password": "password-123"})
    assert response.status_code == 201, response.text
    return response.json()


def test_login_and_run_ownership_are_user_scoped(workdir):
    outcome = StageOutcome(kind=ResultKind.ANSWER, message="ok")
    app = create_app(_settings(workdir), processor=FakeMessageProcessor(outcome))
    with TestClient(app) as client:
        alice = _register(client, "alice")
        bob = _register(client, "bob")
        alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
        bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}

        assert client.post("/api/auth/login", json={"username": "alice", "password": "password-123"}).status_code == 200
        created = client.post("/api/travel/runs", json={"message": "北京天气"}, headers=alice_headers)
        assert created.status_code == 201
        run_id = created.json()["run_id"]
        assert client.get(f"/api/travel/runs/{run_id}", headers=alice_headers).status_code == 200
        assert client.get(f"/api/travel/runs/{run_id}", headers=bob_headers).status_code == 404
        assert client.get("/api/travel/runs").status_code == 405
        assert client.post("/api/travel/runs", json={}).status_code == 401


@pytest.mark.asyncio
async def test_long_term_memory_is_bounded_and_replaces_previous_preference(workdir):
    repository = SQLiteUserRepository(workdir / "memory.sqlite")
    user = await repository.create_user("traveler", "hash")
    service = MemoryService(repository)

    await service.remember_explicit_fields(user.id, {"preferences": ["博物馆", "慢节奏"], "accommodation": "安静酒店"}, "run-1")
    await service.remember_explicit_fields(user.id, {"accommodation": "地铁站附近酒店"}, "run-2")
    stored = await repository.list_memories(user.id)
    assert {item.memory_key for item in stored} == {"preferences", "accommodation"}
    accommodation = next(item for item in stored if item.memory_key == "accommodation")
    assert accommodation.value == "地铁站附近酒店"

    context = await service.context_for(user.id, "下个月帮我安排一次旅行")
    assert len(context) == 2
    assert all(len(item["summary"]) <= service.max_summary_chars for item in context)
    assert "run-1" not in str(context)


def test_protected_routes_reject_missing_invalid_and_expired_tokens(workdir, monkeypatch):
    outcome = StageOutcome(kind=ResultKind.ANSWER, message="ok")
    app = create_app(_settings(workdir, access_token_ttl_minutes=-1), processor=FakeMessageProcessor(outcome))
    with TestClient(app) as client:
        assert client.post("/api/travel/runs", json={}).status_code == 401
        assert client.post("/api/travel/runs", json={}, headers={"Authorization": "Bearer malformed"}).status_code == 401
        registered = _register(client, "expired-user")
        import app.identity.auth as auth_module
        real_now = auth_module.time.time
        monkeypatch.setattr(auth_module.time, "time", lambda: real_now() + 120)
        assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {registered['access_token']}"}).status_code == 401
