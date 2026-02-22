"""
Simplified Pydantic models for overmind_core (open / standalone).

These models intentionally omit Organisation, Role, and RBAC fields.
Enterprise endpoints return the richer models from user.py / token.py / project.py;
core endpoints return these.
"""

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CoreUserModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: UUID
    email: str
    full_name: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None


class CoreProjectModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    project_id: UUID
    name: str
    slug: str
    description: Optional[str] = None
    is_active: bool
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class CoreTokenModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    token_id: UUID
    name: str
    description: Optional[str] = None
    prefix: str
    project_id: UUID
    user_id: UUID
    is_active: bool
    expires_at: Optional[datetime] = None
    last_used_at: Optional[datetime] = None
    allowed_ips: Optional[List[str]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
