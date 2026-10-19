from __future__ import annotations

from datetime import datetime
from sqlalchemy import BigInteger, JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserMemoryRow(Base):
    __tablename__ = "user_memories"
    __table_args__ = (UniqueConstraint("user_id", "memory_key", name="uq_memory_user_key"), Index("ix_memory_user_updated", "user_id", "updated_at"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    memory_key: Mapped[str] = mapped_column(String(128), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    value_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    summary: Mapped[str] = mapped_column(String(160), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    importance: Mapped[float] = mapped_column(Float, nullable=False)
    source_run_id: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

class ProviderCredentialRow(Base):
    """Encrypted user-owned provider credentials; plaintext never reaches ORM."""
    __tablename__ = "provider_credentials"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_provider_credential_owner"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    key_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False)
    model: Mapped[str | None] = mapped_column(String(160))
    is_active: Mapped[bool] = mapped_column(nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(96))


class RunRow(Base):
    __tablename__ = "runs"
    __table_args__ = (Index("ix_runs_user_created", "user_id", "created_at"), Index("ix_runs_user_id", "user_id", "run_id"))
    id: Mapped[str] = mapped_column("run_id", String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    session_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    kind: Mapped[str | None] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error_code: Mapped[str | None] = mapped_column(String(96))
    data: Mapped[dict | None] = mapped_column(JSON)
    conversation: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RunEventRow(Base):
    __tablename__ = "run_events"
    __table_args__ = (Index("ix_events_run_created", "run_id", "created_at"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="RESTRICT"), primary_key=True)
    seq: Mapped[int] = mapped_column(Integer, primary_key=True)
    event: Mapped[str] = mapped_column(String(96), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CheckpointRow(Base):
    __tablename__ = "checkpoints"
    __table_args__ = (UniqueConstraint("user_id", "run_id", name="uq_checkpoint_owner_run"), Index("ix_checkpoint_user_run", "user_id", "run_id"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.run_id", ondelete="RESTRICT"), nullable=False)
    graph_id: Mapped[str] = mapped_column(String(160), nullable=False)
    graph_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class LLMCallRow(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        Index("ix_llm_calls_user_created", "user_id", "created_at"),
        Index("ix_llm_calls_run_stage", "run_id", "stage"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    run_id: Mapped[str | None] = mapped_column(ForeignKey("runs.run_id", ondelete="RESTRICT"))
    graph_id: Mapped[str | None] = mapped_column(String(160))
    graph_version: Mapped[int | None] = mapped_column(Integer)
    task_id: Mapped[str | None] = mapped_column(String(128))
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str | None] = mapped_column(String(160))
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LLMCallAttemptRow(Base):
    __tablename__ = "llm_call_attempts"
    __table_args__ = (UniqueConstraint("call_id", "attempt_no", name="uq_llm_call_attempt"), Index("ix_llm_attempt_call", "call_id"))
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    call_id: Mapped[str] = mapped_column(ForeignKey("llm_calls.id", ondelete="RESTRICT"), nullable=False)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(160))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(96))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class UserQuotaAccountRow(Base):
    __tablename__="user_quota_accounts"
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="RESTRICT"),primary_key=True)
    total_points: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    reserved_points: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    consumed_points: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    version: Mapped[int]=mapped_column(BigInteger,nullable=False,default=0)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
    updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
class QuotaReservationRow(Base):
    __tablename__="quota_reservations"
    __table_args__=(UniqueConstraint("llm_call_id",name="uq_reservation_call"),Index("ix_reservation_user_status","user_id","status"))
    id: Mapped[str]=mapped_column(String(32),primary_key=True)
    user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    llm_call_id: Mapped[str]=mapped_column(ForeignKey("llm_calls.id",ondelete="RESTRICT"),nullable=False)
    provider: Mapped[str]=mapped_column(String(64),nullable=False); model: Mapped[str]=mapped_column(String(160),nullable=False)
    pricing_version: Mapped[str]=mapped_column(String(64),nullable=False); pricing_snapshot: Mapped[dict]=mapped_column(JSON,nullable=False)
    estimated_points: Mapped[int]=mapped_column(BigInteger,nullable=False); actual_points: Mapped[int|None]=mapped_column(BigInteger)
    reserved_points: Mapped[int]=mapped_column(Integer,nullable=False); status: Mapped[str]=mapped_column(String(32),nullable=False)
    reconciliation_reason: Mapped[str|None]=mapped_column(String(96)); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False); updated_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False); settled_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True))
class QuotaLedgerRow(Base):
    __tablename__="quota_ledger"
    __table_args__=(UniqueConstraint("idempotency_key",name="uq_ledger_idempotency"),Index("ix_ledger_user_created","user_id","created_at"),Index("ix_ledger_call","llm_call_id"))
    id: Mapped[str]=mapped_column(String(32),primary_key=True); user_id: Mapped[str]=mapped_column(ForeignKey("users.id",ondelete="RESTRICT"),nullable=False)
    reservation_id: Mapped[str|None]=mapped_column(ForeignKey("quota_reservations.id",ondelete="RESTRICT")); llm_call_id: Mapped[str|None]=mapped_column(ForeignKey("llm_calls.id",ondelete="RESTRICT"))
    event_type: Mapped[str]=mapped_column(String(32),nullable=False); total_delta: Mapped[int]=mapped_column(Integer,nullable=False); reserved_delta: Mapped[int]=mapped_column(Integer,nullable=False); consumed_delta: Mapped[int]=mapped_column(Integer,nullable=False); idempotency_key: Mapped[str]=mapped_column(String(128),nullable=False)
    total_after: Mapped[int]=mapped_column(Integer,nullable=False); reserved_after: Mapped[int]=mapped_column(Integer,nullable=False); consumed_after: Mapped[int]=mapped_column(Integer,nullable=False); reason_code: Mapped[str|None]=mapped_column(String(96)); metadata_json: Mapped[dict|None]=mapped_column(JSON); created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),nullable=False)
