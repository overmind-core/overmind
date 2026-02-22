"""
Pydantic model for Organisation entity.
"""

from pydantic import BaseModel, ConfigDict
from typing import Optional, Dict, Any, List
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
    description: Optional[str] = None
    is_active: bool
    settings: Optional[Dict[str, Any]] = None
    sign_on_method: str
    sign_on_config: Optional[Dict[str, Any]] = None
    domains: Optional[List[str]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
