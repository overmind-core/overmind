"""
Pydantic model for Project entity.
"""

from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any
from datetime import datetime
from uuid import UUID


class ProjectModel(BaseModel):
    """
    Pydantic model for Project entity used in authentication and authorization.

    organisation_id is optional — only set in enterprise mode.
    """

    model_config = ConfigDict(from_attributes=True)

    project_id: UUID
    name: str
    slug: str
    description: Optional[str] = None
    organisation_id: Optional[UUID] = None
    is_active: bool
    settings: Optional[Dict[str, Any]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
