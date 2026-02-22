"""
Core User model.

Enterprise adds Organisation model and patches additional relationships
(organisations, user_roles, sent_invitations, accepted_invitations) via
rbac_extensions.py.
"""

from sqlalchemy import Column, String, Boolean, DateTime, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from overmind_core.db.base import Base
from .relationships import user_project_association
from .enums import SignOnMethod
import uuid


class User(Base):
    __tablename__ = "users"

    user_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        unique=True,
        index=True,
        nullable=False,
        default=uuid.uuid4,
    )
    email = Column(String, unique=True, index=True, nullable=False)
    full_name = Column(String, nullable=True)
    hashed_password = Column(String, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False)
    is_verified = Column(Boolean, default=False, nullable=False)

    sign_on_method = Column(String, nullable=False, default=SignOnMethod.PASSWORD.value)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    last_login = Column(DateTime(timezone=True), nullable=True)

    avatar_url = Column(String, nullable=True)
    timezone = Column(String, default="UTC", nullable=False)

    # Core relationships
    projects = relationship(
        "Project", secondary=user_project_association, back_populates="users"
    )
    tokens = relationship("Token", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            sign_on_method.in_([e.value for e in SignOnMethod]),
            name="ck_user_sign_on_method",
        ),
    )
