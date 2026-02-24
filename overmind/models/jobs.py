"""
Job model - tracks Celery task execution status for frontend visibility.
"""

import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID, JSONB

from overmind.db.base import Base


class Job(Base):
    __tablename__ = "jobs"

    job_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # agent_discovery | judge_scoring | prompt_tuning | model_backtesting
    job_type = Column(String, nullable=False, index=True)

    # For per-prompt jobs; null for project-wide jobs like agent_discovery
    prompt_slug = Column(String, nullable=True, index=True)

    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id"),
        nullable=False,
        index=True,
    )

    # pending | running | completed | failed | cancelled
    status = Column(String, nullable=False, default="pending")

    celery_task_id = Column(String, nullable=True)

    # Stores result data or error info
    result = Column(JSONB, nullable=True)

    # who triggered the job, null for system triggered jobs
    triggered_by_user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=True
    )

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(
        DateTime(timezone=True), onupdate=func.now(), server_default=func.now()
    )
