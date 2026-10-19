"""add immutable llm call telemetry

Revision ID: a18c3e9d4b71
Revises: d56b9c58a7a0
"""
from alembic import op
import sqlalchemy as sa
revision = "a18c3e9d4b71"
down_revision = "d56b9c58a7a0"
branch_labels = None
depends_on = None
def upgrade():
    op.create_table("llm_calls", sa.Column("id", sa.String(32), primary_key=True), sa.Column("user_id", sa.String(32), sa.ForeignKey("users.id", ondelete="RESTRICT")), sa.Column("run_id", sa.String(32), sa.ForeignKey("runs.run_id", ondelete="RESTRICT")), sa.Column("graph_id", sa.String(160)), sa.Column("graph_version", sa.Integer()), sa.Column("task_id", sa.String(128)), sa.Column("stage", sa.String(32), nullable=False), sa.Column("model", sa.String(160)), sa.Column("provider", sa.String(64), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("attempt_count", sa.Integer(), nullable=False), sa.Column("prompt_tokens", sa.Integer(), nullable=False), sa.Column("completion_tokens", sa.Integer(), nullable=False), sa.Column("total_tokens", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.Column("completed_at", sa.DateTime(timezone=True)))
    op.create_index("ix_llm_calls_user_created", "llm_calls", ["user_id", "created_at"])
    op.create_index("ix_llm_calls_run_stage", "llm_calls", ["run_id", "stage"])
    op.create_table("llm_call_attempts", sa.Column("id", sa.String(32), primary_key=True), sa.Column("call_id", sa.String(32), sa.ForeignKey("llm_calls.id", ondelete="RESTRICT"), nullable=False), sa.Column("attempt_no", sa.Integer(), nullable=False), sa.Column("kind", sa.String(32), nullable=False), sa.Column("status", sa.String(24), nullable=False), sa.Column("provider_request_id", sa.String(160)), sa.Column("latency_ms", sa.Integer()), sa.Column("prompt_tokens", sa.Integer(), nullable=False), sa.Column("completion_tokens", sa.Integer(), nullable=False), sa.Column("total_tokens", sa.Integer(), nullable=False), sa.Column("error_code", sa.String(96)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False), sa.UniqueConstraint("call_id", "attempt_no", name="uq_llm_call_attempt"))
    op.create_index("ix_llm_attempt_call", "llm_call_attempts", ["call_id"])
def downgrade():
    op.drop_index("ix_llm_attempt_call", table_name="llm_call_attempts"); op.drop_table("llm_call_attempts"); op.drop_index("ix_llm_calls_run_stage", table_name="llm_calls"); op.drop_index("ix_llm_calls_user_created", table_name="llm_calls"); op.drop_table("llm_calls")
