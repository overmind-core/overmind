"""
Simplified Pydantic models for overmind (open / standalone).

These models intentionally omit Organisation, Role, and RBAC fields.
Enterprise endpoints return the richer models from user.py / token.py / project.py;
core endpoints return these.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CoreUserModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    email: str
    full_name: str | None = None
    is_active: bool
    created_at: datetime | None = None


class CoreProjectModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: UUID
    name: str
    slug: str
    description: str | None = None
    is_active: bool
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CoreTokenModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    token_id: UUID
    name: str
    description: str | None = None
    prefix: str
    project_id: UUID
    user_id: UUID
    is_active: bool
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    allowed_ips: list[str] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
