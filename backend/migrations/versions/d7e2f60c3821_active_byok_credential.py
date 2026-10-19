"""add explicit active BYOK credential

Revision ID: d7e2f60c3821
Revises: c6f1a59b2710
"""
from alembic import op
import sqlalchemy as sa
revision="d7e2f60c3821"; down_revision="c6f1a59b2710"; branch_labels=None; depends_on=None
def upgrade():
    op.add_column("provider_credentials",sa.Column("is_active",sa.Boolean(),nullable=False,server_default=sa.false()))
    # Existing installations with one or more records require an explicit choice.
def downgrade(): op.drop_column("provider_credentials","is_active")
