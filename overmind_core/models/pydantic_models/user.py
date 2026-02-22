"""
Pydantic model for User entity.
"""

from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime
from uuid import UUID


from .project import ProjectModel
from .organisation import OrganisationModel


class UserBaseModel(BaseModel):
    """
    Base Pydantic model for User entity without relationships.
    Use this when you only need user data without related entities.
    """

    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    email: str
    full_name: Optional[str] = None
    is_active: bool
    is_verified: bool
    sign_on_method: str
    avatar_url: Optional[str] = None
    timezone: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    last_login: Optional[datetime] = None


class UserModel(UserBaseModel):
    """
    Full Pydantic model for User entity with relationships.
    Use this when you need user data along with their projects and organisations.
    """

    # Relationships - these are loaded when needed via selectinload
    projects: Optional[List[ProjectModel]] = None
    organisations: Optional[List[OrganisationModel]] = None
