"""
Pydantic model for Token entity.
"""

from pydantic import BaseModel, ConfigDict
from typing import Any
from datetime import datetime
from uuid import UUID
from .user import UserBaseModel
from .organisation import OrganisationModel
from .project import ProjectModel


class TokenModel(BaseModel):
    """
    Pydantic model for Token entity used in authentication and authorization.
    Includes nested relationships for user, organisation, and project.
    Note: Uses UserBaseModel to avoid loading unnecessary user relationships.

    organisation_id and organisation are optional — they are only set in
    enterprise mode.
    """

    model_config = ConfigDict(from_attributes=True)

    token_id: UUID
    name: str
    description: str | None = None
    user_id: UUID
    organisation_id: UUID | None = None
    project_id: UUID
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
    organisation: OrganisationModel | None = None
    project: ProjectModel
