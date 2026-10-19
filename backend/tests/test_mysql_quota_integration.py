from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import Settings
from app.persistence.database import create_session_factory, dispose_session_factory
from app.persistence.mysql_repository import MySQLUserRepository
from app.persistence.sqlalchemy_models import LLMCallRow
from app.quota import MySQLQuotaRepository, Pricing
from app.quota.service import QuotaInsufficient
from app.telemetry.repository import MySQLLLMTelemetryRepository

pytestmark = pytest.mark.skipif(os.getenv("RUN_MYSQL_TESTS") != "1", reason="requires RUN_MYSQL_TESTS=1 and local MySQL")

PRICING = Pricing("test-v1", "openai_compatible", "test", 1_000_000, 1_000_000)

def _settings():
    return Settings(llm_api_key="test", llm_base_url="https://llm.invalid/v1", llm_model="test",
        amap_api_key="", auth_required=True, auth_secret_key="mysql-integration-test-secret-long-enough",
        database_url=os.environ["DATABASE_URL"])

async def _call(sessions: async_sessionmaker, user_id: str, call_id: str):
    from datetime import UTC, datetime
    async with sessions() as session:
        async with session.begin():
            session.add(LLMCallRow(id=call_id, user_id=user_id, run_id=None, graph_id=None, graph_version=None,
                task_id=None, stage="TEST", model="test", provider="openai_compatible", status="CREATED",
                attempt_count=0, prompt_tokens=0, completion_tokens=0, total_tokens=0,
                created_at=datetime.now(UTC), completed_at=None))

@pytest.mark.asyncio
async def test_mysql_quota_reserve_settle_is_idempotent_and_tenant_scoped():
    sessions = create_session_factory(_settings())
    try:
        suffix = uuid.uuid4().hex[:10]
        users = MySQLUserRepository(sessions, default_new_user_points=100)
        owner = await users.create_user("quota_a_" + suffix, "hash")
        other = await users.create_user("quota_b_" + suffix, "hash")
        call_id = uuid.uuid4().hex
        await _call(sessions, owner.id, call_id)
        repo = MySQLQuotaRepository(sessions)
        first = await repo.reserve(owner.id, call_id, "openai_compatible", "test", 80, PRICING)
        duplicate = await repo.reserve(owner.id, call_id, "openai_compatible", "test", 80, PRICING)
        assert first.id == duplicate.id and first.status == "RESERVED"
        settled = await repo.settle(call_id, {"prompt_tokens": 10, "completion_tokens": 10})
        again = await repo.settle(call_id, {"prompt_tokens": 10, "completion_tokens": 10})
        assert settled.status == again.status == "SETTLED"
        account = await repo.get_account_for_user(owner.id)
        assert account["reserved_points"] == 0 and account["consumed_points"] == 20
        assert await repo.get_ledger_for_user(other.id) != await repo.get_ledger_for_user(owner.id)
    finally:
        await dispose_session_factory(sessions)

@pytest.mark.asyncio
async def test_mysql_quota_concurrent_reservation_cannot_overdraw():
    sessions = create_session_factory(_settings())
    try:
        suffix = uuid.uuid4().hex[:10]
        user = await MySQLUserRepository(sessions, default_new_user_points=100).create_user("quota_c_" + suffix, "hash")
        call_a, call_b = uuid.uuid4().hex, uuid.uuid4().hex
        await _call(sessions, user.id, call_a); await _call(sessions, user.id, call_b)
        repo = MySQLQuotaRepository(sessions)
        results = await asyncio.gather(
            repo.reserve(user.id, call_a, "openai_compatible", "test", 80, PRICING),
            repo.reserve(user.id, call_b, "openai_compatible", "test", 80, PRICING),
            return_exceptions=True,
        )
        assert sum(not isinstance(result, Exception) for result in results) == 1
        assert sum(isinstance(result, QuotaInsufficient) for result in results) == 1, repr(results)
        account = await repo.get_account_for_user(user.id)
        assert account["available_points"] == 20
    finally:
        await dispose_session_factory(sessions)

@pytest.mark.asyncio
async def test_mysql_telemetry_attempts_aggregate_and_reconciliation_settles_once():
    sessions = create_session_factory(_settings())
    try:
        suffix = uuid.uuid4().hex[:10]
        user = await MySQLUserRepository(sessions, default_new_user_points=100).create_user("quota_t_" + suffix, "hash")
        from app.telemetry.gateway import LLMCallContext
        call_id = uuid.uuid4().hex
        telemetry = MySQLLLMTelemetryRepository(sessions)
        await telemetry.create_logical_call(call_id, LLMCallContext(stage="PLANNER", user_id=user.id), "test", "openai_compatible")
        quota = MySQLQuotaRepository(sessions)
        await quota.reserve(user.id, call_id, "openai_compatible", "test", 80, PRICING)
        await telemetry.complete_logical_call(call_id, "FAILED", [
            {"kind":"structured", "status":"FAILED", "usage":{"prompt_tokens":10,"completion_tokens":0}},
            {"kind":"structured", "status":"FAILED", "usage":{"prompt_tokens":20,"completion_tokens":5}},
        ])
        from app.quota import ReconciliationService
        outcome = await ReconciliationService(quota, telemetry).reconcile(call_id)
        duplicate = await ReconciliationService(quota, telemetry).reconcile(call_id)
        assert outcome.status == duplicate.status == "SETTLED"
        assert (await quota.get_account_for_user(user.id))["consumed_points"] == 35
    finally:
        await dispose_session_factory(sessions)
