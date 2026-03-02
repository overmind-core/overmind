"""
Pydantic model for Project entity.
"""

from pydantic import BaseModel, ConfigDict
from typing import Any
from datetime import datetime
import uuid


class ProjectModel(BaseModel):
    """
    Pydantic model for Project entity used in authentication and authorization.

    organisation_id is optional — only set in enterprise mode.
    """

    model_config = ConfigDict(from_attributes=True)

    project_id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    organisation_id: str | None = None
    is_active: bool
    settings: dict[str, Any] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
