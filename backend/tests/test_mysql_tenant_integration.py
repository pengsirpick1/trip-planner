from __future__ import annotations
import asyncio
import os
import uuid
import pytest
from fastapi.testclient import TestClient
from app.api.main import create_app
from app.application.travel_service import ResultKind, StageOutcome
from app.config import Settings
from app.domain import GraphExecutionState, TaskGraph
from app.runtime.mysql_state_store import ConcurrentCheckpointUpdateError, MySQLStateStore
from app.persistence.database import create_session_factory, dispose_session_factory
from app.persistence.mysql_repository import MySQLUserRepository, MySQLRunRepository
from app.application.travel_service import RunRecord, RunStatus
pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1", reason="requires RUN_MYSQL_TESTS=1 and local MySQL")
class _Processor:
    async def process(self, message, run): return StageOutcome(kind=ResultKind.ANSWER, message="ok")
def _settings():
    return Settings(llm_api_key="test", llm_base_url="https://llm.invalid/v1", llm_model="test", amap_api_key="", auth_required=True, auth_secret_key="mysql-integration-test-secret-long-enough", database_url=os.environ["DATABASE_URL"])
def test_mysql_tenant_boundary_for_run_routes():
    suffix = uuid.uuid4().hex[:10]
    app = create_app(_settings(), processor=_Processor())
    with TestClient(app) as client:
        def register(name):
            response = client.post("/api/auth/register", json={"username": name, "password": "password-123"})
            assert response.status_code == 201, response.text
            return {"Authorization": "Bearer " + response.json()["access_token"]}, response.json()["user"]["id"]
        a_headers, a_id = register("mysql_a_" + suffix); b_headers, _ = register("mysql_b_" + suffix)
        created = client.post("/api/travel/runs", json={"message": "北京天气"}, headers=a_headers)
        assert created.status_code == 201, created.text
        run_id = created.json()["run_id"]
        assert client.get(f"/api/travel/runs/{run_id}", headers=a_headers).status_code == 200
        assert client.get(f"/api/travel/runs/{run_id}", headers=b_headers).status_code == 404
        assert client.post(f"/api/travel/runs/{run_id}/messages", json={"message": "继续"}, headers=b_headers).status_code == 404
        assert client.get(f"/api/travel/runs/{run_id}/events", headers=b_headers).status_code == 404
        assert client.get(f"/api/travel/runs/{run_id}/trace", headers=b_headers).status_code == 404


@pytest.mark.asyncio
async def test_mysql_checkpoint_is_owner_scoped_and_uses_cas():
    sessions = create_session_factory(_settings())
    try:
        users = MySQLUserRepository(sessions)
        runs = MySQLRunRepository(sessions)
        suffix = uuid.uuid4().hex[:10]
        owner = await users.create_user("cas_a_" + suffix, "hash")
        other = await users.create_user("cas_b_" + suffix, "hash")
        run_id = "casrun" + suffix
        await runs.create(RunRecord(run_id=run_id, user_id=owner.id, status=RunStatus.PENDING))
        store = MySQLStateStore(sessions)
        state = GraphExecutionState(session_id=run_id, graph=TaskGraph(graph_id="cas", tasks=[]), task_statuses={}, current_status="succeeded")
        await store.save(state, user_id=owner.id, run_id=run_id)
        assert await store.load(run_id, user_id=other.id) is None
        first = await store.load(run_id, user_id=owner.id)
        stale = await store.load(run_id, user_id=owner.id)
        await store.save(first, user_id=owner.id, run_id=run_id)
        with pytest.raises(ConcurrentCheckpointUpdateError):
            await store.save(stale, user_id=owner.id, run_id=run_id)
    finally:
        await dispose_session_factory(sessions)
