"""
Core Token model for API authentication.

In core/standalone mode, organisation_id is always NULL.
Enterprise adds the FK constraint, organisation relationship, and
token_roles relationship via rbac_extensions.py and enterprise migrations.
"""

from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    JSON,
    ForeignKey,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from overmind_core.db.base import Base
import uuid


class Token(Base):
    __tablename__ = "tokens"

    token_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        unique=True,
        index=True,
        nullable=False,
        default=uuid.uuid4,
    )
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)

    user_id = Column(UUID(as_uuid=True), ForeignKey("users.user_id"), nullable=False)
    organisation_id = Column(
        UUID(as_uuid=True),
        nullable=True,
        default=None,
    )
    project_id = Column(
        UUID(as_uuid=True), ForeignKey("projects.project_id"), nullable=False
    )

    token_hash = Column(String, nullable=False, unique=True, index=True)
    prefix = Column(String, nullable=False)

    is_active = Column(Boolean, default=True, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    allowed_ips = Column(JSON, nullable=True)
    rate_limit = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Core relationships
    user = relationship("User", back_populates="tokens")
    project = relationship("Project", back_populates="tokens")

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "organisation_id",
            "project_id",
            "name",
            name="uq_token_user_org_project_name",
            postgresql_nulls_not_distinct=True,
        ),
    )
