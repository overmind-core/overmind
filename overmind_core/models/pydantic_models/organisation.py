"""
Pydantic model for Organisation entity.
"""

from pydantic import BaseModel, ConfigDict
from typing import Any
from datetime import datetime
from uuid import UUID


class OrganisationModel(BaseModel):
    """
    Pydantic model for Organisation entity used in authentication and authorization.
    """

    model_config = ConfigDict(from_attributes=True)

    organisation_id: UUID
    name: str
    slug: str
    description: str | None = None
    is_active: bool
    settings: dict[str, Any] | None = None
    sign_on_method: str
    sign_on_config: dict[str, Any] | None = None
    domains: list[str] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
