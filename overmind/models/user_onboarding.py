from sqlalchemy import Column, String, DateTime, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.sql import func
from overmind.db.base import Base
import uuid


class UserOnboarding(Base):
    __tablename__ = "user_onboarding"

    onboarding_id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        unique=True,
        index=True,
        nullable=False,
        default=uuid.uuid4,
    )
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.user_id"),
        nullable=False,
        index=True,
        unique=True,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    step = Column(String, nullable=False)
    status = Column(String, nullable=False)  # completed, in_progress, skipped
    priorities = Column(ARRAY(String), nullable=True)
    description = Column(String, nullable=True)
