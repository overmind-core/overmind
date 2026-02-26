"""add clerk_user_id to users

Revision ID: 0002_add_clerk_user_id_to_users
Revises: 0001_initial_core
Create Date: 2026-02-25 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0002_add_clerk_user_id_to_users"
down_revision = "0001_initial_core"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("clerk_user_id", sa.String(), nullable=True),
    )
    op.create_index("ix_users_clerk_user_id", "users", ["clerk_user_id"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_clerk_user_id", table_name="users")
    op.drop_column("users", "clerk_user_id")
