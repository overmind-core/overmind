"""
Pydantic model for Token entity.
"""

from pydantic import BaseModel, ConfigDict
from typing import Any
from datetime import datetime
import uuid
from .user import UserBaseModel
from .project import ProjectModel


class TokenModel(BaseModel):
    """
    Pydantic model for Token entity used in authentication and authorization.
    Includes nested relationships for user and project.
    Note: Uses UserBaseModel to avoid loading unnecessary user relationships.

    organisation_id is optional — set in enterprise mode as a Clerk org_id string.
    """

    model_config = ConfigDict(from_attributes=True)

    token_id: uuid.UUID
    name: str
    description: str | None = None
    user_id: uuid.UUID
    organisation_id: str | None = None
    project_id: uuid.UUID
    token_hash: str
    prefix: str
    is_active: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    allowed_ips: list[str] | None = None
    rate_limit: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    # Relationships
    user: UserBaseModel
    project: ProjectModel
