"""MySQL-backed virtual quota accounting.

Every public operation owns a fresh AsyncSession.  Provider calls are never
made here, so no database transaction is held across the network boundary.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from .pricing import points
from .service import QuotaInsufficient, Reservation
from ..persistence.sqlalchemy_models import (
    QuotaLedgerRow, QuotaReservationRow, UserQuotaAccountRow,
)


def _now(): return datetime.now(UTC)


def _snapshot(pricing):
    return {"version": pricing.version, "provider": pricing.provider, "model": pricing.model,
            "input_points_per_million": pricing.input_points_per_million,
            "output_points_per_million": pricing.output_points_per_million}


def _reservation(row):
    return Reservation(row.id, row.user_id, row.llm_call_id, row.estimated_points,
                       row.reserved_points, row.status, row.pricing_snapshot)


class MySQLQuotaRepository:
    """Atomic account updates plus append-only ledger writes."""
    def __init__(self, sessions: async_sessionmaker, *, default_new_user_points: int = 0):
        self.sessions = sessions
        self.default_new_user_points = default_new_user_points

    async def ensure_account(self, user_id: str, points_to_credit: int | None = None):
        credit = self.default_new_user_points if points_to_credit is None else points_to_credit
        now = _now()
        async with self.sessions() as session:
            async with session.begin():
                row = await session.scalar(select(UserQuotaAccountRow).where(UserQuotaAccountRow.user_id == user_id).with_for_update())
                if row is None:
                    row = UserQuotaAccountRow(user_id=user_id, total_points=credit, reserved_points=0,
                                              consumed_points=0, version=0, created_at=now, updated_at=now)
                    session.add(row)
                    session.add(QuotaLedgerRow(id=uuid.uuid4().hex, user_id=user_id, reservation_id=None,
                        llm_call_id=None, event_type="CREDIT", total_delta=credit, reserved_delta=0,
                        consumed_delta=0, idempotency_key=f"quota:{user_id}:initial-credit",
                        total_after=credit, reserved_after=0, consumed_after=0,
                        reason_code="INITIAL_CREDIT", metadata_json=None, created_at=now))
                return row

    async def reserve(self, user_id, llm_call_id, provider, model, estimate, pricing):
        estimate = max(int(estimate), int(pricing.minimum_reserve_points))
        now = _now()
        async with self.sessions() as session:
            async with session.begin():
                # Do not lock a missing UNIQUE-key range here.  Two independent
                # calls otherwise acquire different gap locks and then contend
                # for the same account row, which can deadlock in InnoDB.
                existing = await session.scalar(select(QuotaReservationRow).where(QuotaReservationRow.llm_call_id == llm_call_id))
                if existing is not None:
                    if existing.user_id != user_id:
                        raise QuotaInsufficient("额度调用归属不匹配")
                    return _reservation(existing)
                # Avoid SELECT -> Python availability check -> UPDATE races. The
                # conditional update is the concurrency gate for independent app instances.
                changed = await session.execute(update(UserQuotaAccountRow).where(
                    UserQuotaAccountRow.user_id == user_id,
                    UserQuotaAccountRow.total_points - UserQuotaAccountRow.reserved_points - UserQuotaAccountRow.consumed_points >= estimate,
                ).values(reserved_points=UserQuotaAccountRow.reserved_points + estimate,
                         version=UserQuotaAccountRow.version + 1, updated_at=now))
                if changed.rowcount != 1:
                    raise QuotaInsufficient("可用额度不足")
                account = await session.scalar(select(UserQuotaAccountRow).where(UserQuotaAccountRow.user_id == user_id))
                row = QuotaReservationRow(id=uuid.uuid4().hex, user_id=user_id, llm_call_id=llm_call_id,
                    provider=provider, model=model or "default", pricing_version=pricing.version,
                    pricing_snapshot=_snapshot(pricing), estimated_points=estimate, actual_points=None,
                    reserved_points=estimate, status="RESERVED", reconciliation_reason=None,
                    created_at=now, updated_at=now, settled_at=None)
                session.add(row)
                # MySQL enforces the ledger's reservation FK immediately.  A
                # transaction alone does not prescribe ORM flush ordering.
                await session.flush()
                session.add(QuotaLedgerRow(id=uuid.uuid4().hex, user_id=user_id, reservation_id=row.id,
                    llm_call_id=llm_call_id, event_type="RESERVE", total_delta=0, reserved_delta=estimate,
                    consumed_delta=0, idempotency_key=f"quota:{llm_call_id}:reserve", total_after=account.total_points,
                    reserved_after=account.reserved_points, consumed_after=account.consumed_points,
                    reason_code=None, metadata_json={"pricing_version": pricing.version}, created_at=now))
                return _reservation(row)

    @staticmethod
    def _usage_points(row, usage):
        snap = row.pricing_snapshot
        class P: pass
        p = P(); p.input_points_per_million = snap["input_points_per_million"]; p.output_points_per_million = snap["output_points_per_million"]
        return points(p, int((usage or {}).get("prompt_tokens", 0) or 0), int((usage or {}).get("completion_tokens", 0) or 0))

    async def settle(self, llm_call_id, usage):
        now = _now()
        async with self.sessions() as session:
            async with session.begin():
                row = await session.scalar(select(QuotaReservationRow).where(QuotaReservationRow.llm_call_id == llm_call_id).with_for_update())
                if row is None: raise KeyError(llm_call_id)
                if row.status == "SETTLED": return _reservation(row)
                if row.status != "RESERVED": return _reservation(row)
                actual = self._usage_points(row, usage)
                if actual > row.reserved_points:
                    row.status="PENDING_RECONCILIATION"; row.actual_points=actual; row.reconciliation_reason="ACTUAL_EXCEEDS_RESERVE"; row.updated_at=now
                    return _reservation(row)
                account = await session.scalar(select(UserQuotaAccountRow).where(UserQuotaAccountRow.user_id == row.user_id).with_for_update())
                account.reserved_points -= row.reserved_points; account.consumed_points += actual; account.version += 1; account.updated_at=now
                row.actual_points=actual; row.reserved_points=0; row.status="SETTLED"; row.settled_at=now; row.updated_at=now
                session.add(QuotaLedgerRow(id=uuid.uuid4().hex,user_id=row.user_id,reservation_id=row.id,llm_call_id=row.llm_call_id,
                    event_type="SETTLE",total_delta=0,reserved_delta=-row.estimated_points,consumed_delta=actual,
                    idempotency_key=f"quota:{llm_call_id}:settle",total_after=account.total_points,reserved_after=account.reserved_points,
                    consumed_after=account.consumed_points,reason_code=None,metadata_json={"actual_points":actual},created_at=now))
                return _reservation(row)

    async def release(self, llm_call_id, reason="NO_COST"):
        now = _now()
        async with self.sessions() as session:
            async with session.begin():
                row=await session.scalar(select(QuotaReservationRow).where(QuotaReservationRow.llm_call_id==llm_call_id).with_for_update())
                if row is None: raise KeyError(llm_call_id)
                if row.status in {"RELEASED", "SETTLED", "PENDING_RECONCILIATION"}: return _reservation(row)
                account=await session.scalar(select(UserQuotaAccountRow).where(UserQuotaAccountRow.user_id==row.user_id).with_for_update())
                held=row.reserved_points; account.reserved_points-=held; account.version+=1; account.updated_at=now
                row.reserved_points=0; row.status="RELEASED"; row.reconciliation_reason=reason; row.updated_at=now
                session.add(QuotaLedgerRow(id=uuid.uuid4().hex,user_id=row.user_id,reservation_id=row.id,llm_call_id=llm_call_id,event_type="RELEASE",total_delta=0,reserved_delta=-held,consumed_delta=0,idempotency_key=f"quota:{llm_call_id}:release",total_after=account.total_points,reserved_after=account.reserved_points,consumed_after=account.consumed_points,reason_code=reason,metadata_json=None,created_at=now))
                return _reservation(row)

    async def mark_pending(self, llm_call_id, reason):
        async with self.sessions() as session:
            async with session.begin():
                row=await session.scalar(select(QuotaReservationRow).where(QuotaReservationRow.llm_call_id==llm_call_id).with_for_update())
                if row is None: raise KeyError(llm_call_id)
                if row.status == "RESERVED": row.status="PENDING_RECONCILIATION"; row.reconciliation_reason=reason; row.updated_at=_now()
                return _reservation(row)

    async def get_account_for_user(self, user_id):
        async with self.sessions() as s:
            row=await s.scalar(select(UserQuotaAccountRow).where(UserQuotaAccountRow.user_id==user_id))
            if row is None: return None
            return {"total_points":row.total_points,"reserved_points":row.reserved_points,"consumed_points":row.consumed_points,"available_points":row.total_points-row.reserved_points-row.consumed_points,"version":row.version}

    async def get_ledger_for_user(self, user_id, limit=50):
        async with self.sessions() as s:
            rows=(await s.scalars(select(QuotaLedgerRow).where(QuotaLedgerRow.user_id==user_id).order_by(QuotaLedgerRow.created_at.desc()).limit(max(1,min(int(limit),100))))).all()
            return [{"id":r.id,"llm_call_id":r.llm_call_id,"event_type":r.event_type,"total_delta":r.total_delta,"reserved_delta":r.reserved_delta,"consumed_delta":r.consumed_delta,"total_after":r.total_after,"reserved_after":r.reserved_after,"consumed_after":r.consumed_after,"reason_code":r.reason_code,"created_at":r.created_at} for r in rows]
