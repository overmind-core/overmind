from sqlalchemy import (
    Column,
    String,
    DateTime,
    ForeignKey,
    PrimaryKeyConstraint,
    Integer,
)
from sqlalchemy.sql import func
from overmind.db.base import Base
from sqlalchemy.dialects.postgresql import UUID, JSONB

# Prompt version lifecycle statuses:
#   active     — currently serving production traffic; exactly one per (slug, project)
#   pending    — AI-generated candidate awaiting user review / acceptance
#   superseded — was previously active but replaced by a newer accepted version
#   rejected   — user explicitly dismissed this candidate
PROMPT_STATUS_ACTIVE = "active"
PROMPT_STATUS_PENDING = "pending"
PROMPT_STATUS_SUPERSEDED = "superseded"
PROMPT_STATUS_REJECTED = "rejected"


class Prompt(Base):
    __tablename__ = "prompts"

    slug = Column(String, nullable=False)
    hash = Column(String, nullable=False)
    prompt = Column(String, nullable=False)
    display_name = Column(String(255), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.project_id"), nullable=False
    )
    version = Column(Integer, nullable=False, default=1)

    # Evaluation criteria stored as JSON
    # Format: {"metric_name": ["rule1", "rule2", ...]}
    # Example: {"correctness": ["Must provide accurate information", "Must be complete"]}
    evaluation_criteria = Column(JSONB, nullable=True)

    # Improvement metadata tracking prompt optimization history
    # Format: {
    #   "last_improvement_span_count": 100,
    #   "criteria_invalidated": true,   # set when criteria/description changes; cleared after improvement runs
    #   "improvement_history": [
    #     {
    #       "span_count": 50,
    #       "new_version": 2,
    #       "timestamp": "2026-02-09T10:30:00Z",
    #       "spans_used": 45
    #     }
    #   ]
    # }
    improvement_metadata = Column(JSONB, nullable=True)

    # Agent description and feedback history
    # Format: {
    #   "description": "Agent does X...",
    #   "feedback_history": [
    #     {"span_id": "...", "feedback": "positive/negative", "timestamp": "..."}
    #   ],
    #   "last_review_span_count": 10,
    #   "next_review_span_count": 100
    # }
    agent_description = Column(JSONB, nullable=True)

    # LLM-generated model recommendations based on agent description.
    # Generated immediately after agent description creation so users can see
    # suggestions before backtesting jobs are triggered.
    # Format: {
    #   "generated_at": "2026-03-05T10:00:00Z",
    #   "recommendations": [
    #     {
    #       "model": "claude-sonnet-4-6",
    #       "provider": "anthropic",
    #       "category": "best_overall",
    #       "reason": "Excels at nuanced reasoning..."
    #     }
    #   ],
    #   "summary": "Based on the agent's tasks..."
    # }
    backtest_model_suggestions = Column(JSONB, nullable=True)

    # Backtesting metadata tracking threshold state for the backtesting pipeline.
    # Format: {
    #   "last_backtest_span_count": 120,
    #   "criteria_invalidated": true,   # set when criteria/description changes; cleared after backtest runs
    # }
    backtest_metadata = Column(JSONB, nullable=True)

    # User-defined categorisation tags, e.g. ["HR", "financial"]
    # Stored as a JSON array of strings; shared across all versions of the same slug
    tags = Column(JSONB, nullable=True)

    # Version lifecycle status. See PROMPT_STATUS_* constants above.
    # Exactly one version per (slug, project_id) has status='active' at any time.
    status = Column(
        String(20),
        nullable=False,
        default=PROMPT_STATUS_ACTIVE,
        server_default=PROMPT_STATUS_ACTIVE,
    )

    __table_args__ = (
        PrimaryKeyConstraint(
            "slug", "project_id", "version", name="pk_prompt_id_project_version"
        ),
    )

    @property
    def prompt_id(self) -> str:
        """Returns a unique identifier combining project_id, version, and slug."""
        return f"{self.project_id}_{self.version}_{self.slug}"

    @staticmethod
    def parse_prompt_id(prompt_id: str) -> tuple[str, int, str]:
        """
        Parse a prompt_id string into its components.

        Args:
            prompt_id: String in format "{project_id}_{version}_{slug}"

        Returns:
            Tuple of (project_id, version, slug)
        """
        parts = prompt_id.split("_", 2)
        if len(parts) != 3:
            raise ValueError(f"Invalid prompt_id format: {prompt_id}")

        project_id_str, version_str, slug = parts
        try:
            version = int(version_str)
        except ValueError:
            raise ValueError(f"Invalid version in prompt_id: {version_str}")

        return project_id_str, version, slug
