"""
Pydantic model for Token entity.
"""

from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any, List
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
    description: Optional[str] = None
    user_id: UUID
    organisation_id: Optional[UUID] = None
    project_id: UUID
    token_hash: str
    prefix: str
    is_active: bool
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    allowed_ips: Optional[List[str]] = None
    rate_limit: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # Relationships
    user: UserBaseModel
    organisation: Optional[OrganisationModel] = None
    project: ProjectModel
