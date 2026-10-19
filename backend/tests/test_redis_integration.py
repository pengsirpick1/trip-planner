"""Redis Phase 5 integration tests.  They require a local disposable Redis.

Run explicitly: RUN_REDIS_TESTS=1 python -m pytest tests/test_redis_integration.py
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.application.travel_service import ResultKind, StageOutcome
from app.config import Settings
from app.redis_store import InMemoryRedis, RedisStore
from tests.fakes import FakeMessageProcessor


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_REDIS_TESTS") != "1",
    reason="requires local Redis; set RUN_REDIS_TESTS=1 explicitly",
)


def _settings(workdir, **overrides):
    values = {
        "llm_api_key": "test-key",
        "llm_base_url": "https://llm.invalid/v1",
        "llm_model": "test-model",
        "db_path": str(workdir / "redis-integration.sqlite"),
        "amap_api_key": "",
        "auth_required": True,
        "auth_secret_key": "a-test-secret-that-is-longer-than-twenty-four-characters",
        # DB 15 is reserved for this explicit integration suite.
        "redis_url": "redis://127.0.0.1:6379/15",
        "api_rate_limit_requests": 100,
    }
    values.update(overrides)
    return Settings(**values)


def _clear_redis():
    from redis import Redis
    Redis.from_url("redis://127.0.0.1:6379/15", decode_responses=True).flushdb()


def _register(client, username):
    response = client.post("/api/auth/register", json={"username": username, "password": "password-123"})
    assert response.status_code == 201, response.text
    return response.json()


def test_real_redis_run_snapshot_invalidation_and_idempotent_replay(workdir):
    _clear_redis()
    app = create_app(
        _settings(workdir),
        processor=FakeMessageProcessor(StageOutcome(kind=ResultKind.ANSWER, message="ok")),
    )
    with TestClient(app) as client:
        identity = _register(client, "redis-cache-user")
        headers = {"Authorization": f"Bearer {identity['access_token']}"}
        first = client.post("/api/travel/runs", json={}, headers=headers)
        assert first.status_code == 201
        run_id = first.json()["run_id"]

        # First read populates the real Redis snapshot.  Datetimes must be
        # JSON-safe and the cached representation must round-trip through
        # RunRecord validation on the next GET.
        assert client.get(f"/api/travel/runs/{run_id}", headers=headers).status_code == 200
        from redis import Redis
        redis = Redis.from_url("redis://127.0.0.1:6379/15", decode_responses=True)
        cache_key = f"agent:run:{identity['user']['id']}:{run_id}"
        cached = json.loads(redis.get(cache_key))
        assert cached["run_id"] == run_id
        assert isinstance(cached["created_at"], str)

        # A write drops the snapshot; the following read refills from SQLite.
        assert client.post(f"/api/travel/runs/{run_id}/messages", json={"message": "继续优化"}, headers=headers).status_code == 200
        assert redis.get(cache_key) is None
        assert client.get(f"/api/travel/runs/{run_id}", headers=headers).status_code == 200
        assert redis.get(cache_key) is not None

        idem_headers = headers | {"Idempotency-Key": "redis-verified-create-001"}
        created = client.post("/api/travel/runs", json={}, headers=idem_headers)
        replay = client.post("/api/travel/runs", json={}, headers=idem_headers)
        assert created.status_code == replay.status_code == 201
        assert replay.headers["Idempotency-Replayed"] == "true"
        assert replay.json() == created.json()


def test_real_redis_fixed_window_rate_limit_and_tenant_keys(workdir):
    _clear_redis()
    app = create_app(
        _settings(workdir, api_rate_limit_requests=2),
        processor=FakeMessageProcessor(StageOutcome(kind=ResultKind.ANSWER, message="ok")),
    )
    with TestClient(app) as client:
        assert client.get("/api/no-such-resource").status_code == 404
        assert client.get("/api/no-such-resource").status_code == 404
        blocked = client.get("/api/no-such-resource")
        assert blocked.status_code == 429
        assert blocked.json()["code"] == "RATE_LIMITED"

        # A per-user tool bucket does not consume another tenant's allowance.
        import asyncio

        async def assert_tenant_scoping():
            store = await RedisStore.connect("redis://127.0.0.1:6379/15")
            try:
                assert await store.allow("tool:user-a:weather", 1, 60)
                assert not await store.allow("tool:user-a:weather", 1, 60)
                assert await store.allow("tool:user-b:weather", 1, 60)
                # Redis SET NX is the cross-process concurrency primitive
                # behind request idempotency: exactly one concurrent claimant
                # receives the right to execute the request.
                results = await asyncio.gather(*(
                    store.claim("idempotency:concurrent-verification", 60)
                    for _ in range(8)
                ))
                assert sum(results) == 1
            finally:
                await store.close()

        asyncio.run(assert_tenant_scoping())


def test_redis_outage_falls_back_without_losing_run_availability(workdir):
    app = create_app(
        _settings(workdir, redis_url="redis://127.0.0.1:6398/15"),
        processor=FakeMessageProcessor(StageOutcome(kind=ResultKind.ANSWER, message="ok")),
    )
    with TestClient(app) as client:
        assert isinstance(app.state.redis_store, InMemoryRedis)
        assert client.get("/health").status_code == 200
