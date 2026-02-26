"""
Core Project model.

In core/standalone mode, organisation_id is always NULL.
Enterprise adds the FK constraint and the organisation relationship
via rbac_extensions.py and enterprise migrations.
"""

from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    JSON,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from overmind.db.base import Base
from .relationships import user_project_association
import uuid


class Project(Base):
    __tablename__ = "projects"

    project_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        unique=True,
        index=True,
        nullable=False,
        default=uuid.uuid4,
    )
    name = Column(String, nullable=False)
    slug = Column(String, nullable=False, index=True)
    description = Column(String, nullable=False)
    organisation_id = Column(
        String,
        nullable=True,
        default=None,
    )
    is_active = Column(Boolean, default=True, nullable=False)

    settings = Column(JSON, nullable=True, default=dict)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    # Core relationships
    users = relationship(
        "User", secondary=user_project_association, back_populates="projects"
    )
    tokens = relationship(
        "Token", back_populates="project", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint(
            "organisation_id",
            "slug",
            name="uq_project_org_slug",
            postgresql_nulls_not_distinct=True,
        ),
    )
