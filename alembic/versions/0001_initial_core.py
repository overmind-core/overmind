"""initial core schema

Revision ID: 0001_initial_core
Revises:
Create Date: 2026-02-20

Core tables only. Enterprise extends this schema with organisations,
roles, invitations, audit, policies, and oauth tables.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB, BIGINT, ARRAY


revision = "0001_initial_core"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # --- users ---
    op.create_table(
        "users",
        sa.Column("user_id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String, unique=True, index=True, nullable=False),
        sa.Column("full_name", sa.String, nullable=True),
        sa.Column("hashed_password", sa.String, nullable=False),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("is_verified", sa.Boolean, default=False, nullable=False),
        sa.Column(
            "sign_on_method", sa.String, nullable=False, server_default="password"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column("last_login", sa.DateTime(timezone=True), nullable=True),
        sa.Column("avatar_url", sa.String, nullable=True),
        sa.Column(
            "timezone", sa.String, default="UTC", nullable=False, server_default="UTC"
        ),
        sa.CheckConstraint(
            "sign_on_method IN ('password', 'SAML 2.0', 'oauth_google')",
            name="ck_user_sign_on_method",
        ),
    )

    # --- projects ---
    op.create_table(
        "projects",
        sa.Column("project_id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("slug", sa.String, nullable=False, index=True),
        sa.Column("description", sa.String, nullable=False),
        sa.Column("organisation_id", sa.String, nullable=True),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("settings", sa.JSON, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "organisation_id",
            "slug",
            name="uq_project_org_slug",
            postgresql_nulls_not_distinct=True,
        ),
    )

    # --- tokens ---
    op.create_table(
        "tokens",
        sa.Column("token_id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String, nullable=False),
        sa.Column("description", sa.String, nullable=True),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column("organisation_id", sa.String, nullable=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.project_id"),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String, nullable=False, unique=True, index=True),
        sa.Column("prefix", sa.String, nullable=False),
        sa.Column("is_active", sa.Boolean, default=True, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("allowed_ips", sa.JSON, nullable=True),
        sa.Column("rate_limit", sa.JSON, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint(
            "user_id",
            "organisation_id",
            "project_id",
            "name",
            name="uq_token_user_org_project_name",
            postgresql_nulls_not_distinct=True,
        ),
    )

    # --- user_projects (many-to-many) ---
    op.create_table(
        "user_projects",
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            primary_key=True,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.project_id"),
            primary_key=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )

    # --- conversations ---
    op.create_table(
        "conversations",
        sa.Column(
            "conversation_id", UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.project_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # --- traces ---
    op.create_table(
        "traces",
        sa.Column("trace_id", UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("application_name", sa.String(255), nullable=False),
        sa.Column("source", sa.String(255), nullable=False),
        sa.Column("version", sa.String(255), nullable=False),
        sa.Column("start_time_unix_nano", BIGINT, nullable=False),
        sa.Column("end_time_unix_nano", BIGINT, nullable=False),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("input_params", JSONB, nullable=False),
        sa.Column("output_params", JSONB, nullable=True),
        sa.Column("input", JSONB, nullable=False),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("metadata_attributes", JSONB, nullable=False),
        sa.Column("feedback_score", JSONB, nullable=False),
        sa.Column(
            "conversation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("conversations.conversation_id"),
            nullable=True,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.project_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # --- spans ---
    op.create_table(
        "spans",
        sa.Column("span_id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("operation", sa.String, nullable=False),
        sa.Column("start_time_unix_nano", BIGINT, nullable=False),
        sa.Column("end_time_unix_nano", BIGINT, nullable=False),
        sa.Column("input_params", JSONB, nullable=False),
        sa.Column("output_params", JSONB, nullable=True),
        sa.Column("input", JSONB, nullable=False),
        sa.Column("output", JSONB, nullable=True),
        sa.Column("status_code", sa.Integer, nullable=False),
        sa.Column("metadata_attributes", JSONB, nullable=False),
        sa.Column("feedback_score", JSONB, nullable=False),
        sa.Column(
            "trace_id",
            UUID(as_uuid=True),
            sa.ForeignKey("traces.trace_id"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("parent_span_id", sa.String(36), nullable=True),
        sa.Column("prompt_id", sa.String, nullable=True),
    )

    # --- prompts ---
    op.create_table(
        "prompts",
        sa.Column("slug", sa.String, nullable=False),
        sa.Column("hash", sa.String, nullable=False),
        sa.Column("prompt", sa.String, nullable=False),
        sa.Column("display_name", sa.String(255), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
        ),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.project_id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer, nullable=False, server_default="1"),
        sa.Column("evaluation_criteria", JSONB, nullable=True),
        sa.Column("improvement_metadata", JSONB, nullable=True),
        sa.Column("agent_description", JSONB, nullable=True),
        sa.Column("tags", JSONB, nullable=True),
        sa.PrimaryKeyConstraint(
            "slug", "project_id", "version", name="pk_prompt_id_project_version"
        ),
    )

    # --- backtest_runs ---
    op.create_table(
        "backtest_runs",
        sa.Column(
            "backtest_run_id", UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column("prompt_id", sa.String, nullable=False, index=True),
        sa.Column("models", JSONB, nullable=False),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("celery_task_id", sa.String, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- jobs ---
    op.create_table(
        "jobs",
        sa.Column("job_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("job_type", sa.String, nullable=False, index=True),
        sa.Column("prompt_slug", sa.String, nullable=True, index=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.project_id"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("celery_task_id", sa.String, nullable=True),
        sa.Column("result", JSONB, nullable=True),
        sa.Column(
            "triggered_by_user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # --- suggestions ---
    op.create_table(
        "suggestions",
        sa.Column("suggestion_id", UUID(as_uuid=True), primary_key=True),
        sa.Column("prompt_slug", sa.String, nullable=False, index=True),
        sa.Column(
            "project_id",
            UUID(as_uuid=True),
            sa.ForeignKey("projects.project_id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "job_id",
            UUID(as_uuid=True),
            sa.ForeignKey("jobs.job_id"),
            nullable=True,
            index=True,
        ),
        sa.Column("title", sa.String, nullable=False),
        sa.Column("description", sa.String, nullable=False),
        sa.Column("new_prompt_text", sa.String, nullable=True),
        sa.Column("new_prompt_version", sa.Integer, nullable=True),
        sa.Column("scores", JSONB, nullable=True),
        sa.Column("status", sa.String, nullable=False, server_default="pending"),
        sa.Column("vote", sa.Integer, nullable=False, server_default="0"),
        sa.Column("feedback", sa.String, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    # --- user_onboarding ---
    op.create_table(
        "user_onboarding",
        sa.Column(
            "onboarding_id", UUID(as_uuid=True), primary_key=True, nullable=False
        ),
        sa.Column(
            "user_id",
            UUID(as_uuid=True),
            sa.ForeignKey("users.user_id"),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("step", sa.String, nullable=False),
        sa.Column("status", sa.String, nullable=False),
        sa.Column("priorities", ARRAY(sa.String), nullable=True),
        sa.Column("description", sa.String, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("user_onboarding")
    op.drop_table("suggestions")
    op.drop_table("jobs")
    op.drop_table("backtest_runs")
    op.drop_table("prompts")
    op.drop_table("spans")
    op.drop_table("traces")
    op.drop_table("conversations")
    op.drop_table("user_projects")
    op.drop_table("tokens")
    op.drop_table("projects")
    op.drop_table("users")
