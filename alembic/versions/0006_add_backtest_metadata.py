"""add backtest_metadata to prompts

Revision ID: 0006_add_backtest_metadata_to_prompts
Revises: 0005_add_model_suggestions
Create Date: 2026-03-09 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision = "0006_add_backtest_metadata"
down_revision = "0005_add_model_suggestions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("prompts", sa.Column("backtest_metadata", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("prompts", "backtest_metadata")
