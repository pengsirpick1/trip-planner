"""add encrypted per-user provider credentials

Revision ID: c6f1a59b2710
Revises: b4d8e2a6f931
"""
from alembic import op
import sqlalchemy as sa
revision="c6f1a59b2710"; down_revision="b4d8e2a6f931"; branch_labels=None; depends_on=None
def upgrade():
    op.create_table("provider_credentials", sa.Column("id",sa.String(32),primary_key=True),sa.Column("user_id",sa.String(32),sa.ForeignKey("users.id",ondelete="RESTRICT"),nullable=False),sa.Column("provider",sa.String(32),nullable=False),sa.Column("encrypted_api_key",sa.Text(),nullable=False),sa.Column("nonce",sa.String(64),nullable=False),sa.Column("key_version",sa.Integer(),nullable=False),sa.Column("base_url",sa.String(512),nullable=False),sa.Column("model",sa.String(160)),sa.Column("created_at",sa.DateTime(timezone=True),nullable=False),sa.Column("updated_at",sa.DateTime(timezone=True),nullable=False),sa.Column("last_verified_at",sa.DateTime(timezone=True)),sa.Column("last_error_code",sa.String(96)),sa.UniqueConstraint("user_id","provider",name="uq_provider_credential_owner"))
def downgrade(): op.drop_table("provider_credentials")
