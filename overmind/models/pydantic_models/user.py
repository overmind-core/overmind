"""
Pydantic model for User entity.
"""

from pydantic import BaseModel, ConfigDict
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
    full_name: str | None = None
    is_active: bool
    is_verified: bool
    sign_on_method: str
    avatar_url: str | None = None
    timezone: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    last_login: datetime | None = None


class UserModel(UserBaseModel):
    """
    Full Pydantic model for User entity with relationships.
    Use this when you need user data along with their projects and organisations.
    """

    # Relationships - these are loaded when needed via selectinload
    projects: list[ProjectModel] | None = None
    organisations: list[OrganisationModel] | None = None
