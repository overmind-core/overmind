"""
Association tables for many-to-many relationships in the core IAM system.

Core only defines user_project_association.
Enterprise adds user_organisation_association via its own models.
"""

from sqlalchemy import Column, DateTime, ForeignKey, Table
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from overmind.db.base import Base


user_project_association = Table(
    "user_projects",
    Base.metadata,
    Column(
        "user_id", UUID(as_uuid=True), ForeignKey("users.user_id"), primary_key=True
    ),
    Column(
        "project_id",
        UUID(as_uuid=True),
        ForeignKey("projects.project_id"),
        primary_key=True,
    ),
    Column("created_at", DateTime(timezone=True), server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), onupdate=func.now()),
)
