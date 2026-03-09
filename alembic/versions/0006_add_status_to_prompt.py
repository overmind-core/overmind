"""add status to prompt

Revision ID: 0006_add_status_to_prompt
Revises: 0005_add_model_suggestions
Create Date: 2026-03-06 00:00:00.000000

"""

from alembic import op
import sqlalchemy as sa


revision = "0006_add_status_to_prompt"
down_revision = "0005_add_model_suggestions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prompts",
        sa.Column(
            "status",
            sa.String(20),
            nullable=False,
            server_default="active",
        ),
    )
    # All rows start as 'active' via server_default.  Fix: only the highest
    # version per (slug, project_id) should be active; older versions become
    # 'superseded'.  This prevents the "multiple active prompts" invariant
    # violation that caused the original PR #4 to be reverted.
    op.execute(
        """
        UPDATE prompts SET status = 'superseded'
        WHERE (slug, project_id, version) NOT IN (
            SELECT slug, project_id, MAX(version)
            FROM prompts
            GROUP BY slug, project_id
        )
        """
    )


def downgrade() -> None:
    op.drop_column("prompts", "status")
