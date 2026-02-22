"""
Telemetry and Traces DB models for overmind.
"""

from sqlalchemy.sql import func
from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    Integer,
)
from sqlalchemy.dialects.postgresql import BIGINT, UUID, JSONB

from overmind_core.db.base import Base
import uuid


class ConversationModel(Base):
    """
    Conversation stores the meta for a single parent entity containing multiple traces
    """

    __tablename__ = "conversations"

    conversation_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        unique=True,
        index=True,
        nullable=False,
        default=uuid.uuid4,
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class TraceModel(Base):
    __tablename__ = "traces"

    trace_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        unique=True,
        index=True,
        nullable=False,
        default=uuid.uuid4,
    )

    application_name = Column(
        String(255), nullable=False
    )  # optional name which user can pass
    source = Column(
        String(255), nullable=False
    )  # overmind_api, chrome_extension, etc, maybe enum?
    version = Column(
        String(255), nullable=False
    )  # stripe like date based versioning, possibly enum
    start_time_unix_nano = Column(BIGINT, nullable=False)
    end_time_unix_nano = Column(BIGINT, nullable=False)

    status_code = Column(Integer, nullable=False)

    input_params = Column(
        JSONB, nullable=False
    )  # for files, audio, image pdf etc, we store url not b64, objects are stored in s3 if user passed b64
    output_params = Column(
        JSONB, nullable=True
    )  # for files, audio, image pdf etc, we store url not b64, objects are stored in s3 if user passed b64
    input = Column(JSONB, nullable=False, default=dict)
    output = Column(JSONB, nullable=True)

    metadata_attributes = Column(
        JSONB, nullable=False, default=dict
    )  # ResourceAttributes
    feedback_score = Column(JSONB, nullable=False, default=dict)

    conversation_id = Column(
        UUID(as_uuid=True), ForeignKey("conversations.conversation_id"), nullable=True
    )
    project_id = Column(
        UUID(as_uuid=True),
        ForeignKey("projects.project_id"),
        nullable=False,
        index=True,
    )
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False, index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class SpanModel(Base):
    __tablename__ = "spans"

    # Operations used by internal system jobs (prompt tuning, backtesting).
    # Spans with these operations should typically be excluded from user-facing
    # queries, scoring jobs, and analytics.
    _SYSTEM_OPERATIONS = {"prompt_tuning"}
    _SYSTEM_OPERATION_PREFIXES = ("backtest:",)

    span_id = Column(
        String(36),
        primary_key=True,
        unique=True,
        index=True,
        nullable=False,
    )
    operation = Column(String, nullable=False)

    @classmethod
    def exclude_system_spans(cls):
        """Return a list of SQLAlchemy filter clauses that exclude system-generated spans."""
        from sqlalchemy import and_

        return and_(
            cls.operation.notin_(cls._SYSTEM_OPERATIONS),
            ~cls.operation.like("backtest:%"),
            ~cls.operation.like("prompt_tuning"),
        )

    start_time_unix_nano = Column(
        BIGINT, nullable=False
    )  # postgres stores mico seconds precision timestamp
    end_time_unix_nano = Column(BIGINT, nullable=False)

    input_params = Column(JSONB, nullable=False, default=dict)
    output_params = Column(JSONB, nullable=True)

    input = Column(JSONB, nullable=False, default=dict)
    output = Column(JSONB, nullable=True)

    status_code = Column(Integer, nullable=False)

    metadata_attributes = Column(
        JSONB, nullable=False, default=dict
    )  # SpanAttributes, tool.name, tool.platform etc

    feedback_score = Column(JSONB, nullable=False, default=dict)

    trace_id = Column(UUID(as_uuid=True), ForeignKey("traces.trace_id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    parent_span_id = Column(
        String(36), nullable=True
    )  # just to not break the UI for now

    prompt_id = Column(String, nullable=True)


class BacktestRun(Base):
    """
    BacktestRun stores metadata for a backtesting run where multiple models
    are evaluated against historical spans. Individual results are stored as
    spans with backtest metadata.
    """

    __tablename__ = "backtest_runs"

    backtest_run_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        unique=True,
        index=True,
        nullable=False,
        default=uuid.uuid4,
    )
    prompt_id = Column(String, nullable=False, index=True)
    models = Column(JSONB, nullable=False)  # List of model names tested
    status = Column(
        String, nullable=False, default="pending"
    )  # pending | running | completed | failed
    celery_task_id = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    completed_at = Column(DateTime(timezone=True), nullable=True)
