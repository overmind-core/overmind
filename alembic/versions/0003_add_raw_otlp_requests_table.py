"""add raw_otlp_requests table

Revision ID: 0003_add_raw_otlp_requests_table
Revises: 0002_add_clerk_user_id_to_users
Create Date: 2026-02-26 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_add_raw_otlp_requests_table"
down_revision = "0002_add_clerk_user_id_to_users"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "raw_otlp_requests",
        sa.Column(
            "id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("trace_ids", postgresql.JSONB(), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("content_encoding", sa.String(50), nullable=True),
        sa.Column("raw_body", sa.LargeBinary(), nullable=False),
        sa.Column("body_size", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_raw_otlp_requests_project_id",
        "raw_otlp_requests",
        ["project_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_raw_otlp_requests_project_id", table_name="raw_otlp_requests")
    op.drop_table("raw_otlp_requests")
