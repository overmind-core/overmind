"""add model_suggestions to prompts

Revision ID: 0004_add_model_suggestions_to_prompts
Revises: 0003_add_raw_otlp_requests_table
Create Date: 2026-03-05 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0004_add_model_suggestions_to_prompts"
down_revision = "0003_add_raw_otlp_requests_table"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prompts",
        sa.Column("backtest_model_suggestions", JSONB, nullable=True),
    )


def downgrade() -> None:
    op.drop_column("prompts", "backtest_model_suggestions")
