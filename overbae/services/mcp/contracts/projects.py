"""Strict contracts for project and account MCP tools."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel

ExpiryDays = Literal[0, 30, 90, 180, 365]


class CreateApiKeyInput(MCPModel):
    name: str = Field(default="", max_length=255)
    description: str = Field(default="", max_length=2_000)
    expires_in_days: ExpiryDays = 365
    project_id: str | None = None


class CreateApiKeyOutput(MCPModel):
    id: str
    key: str = Field(
        min_length=1,
        max_length=128,
        description="Plaintext secret. Returned only in this response.",
    )
    name: str = Field(max_length=255)
    description: str = Field(max_length=2_000)
    prefix: str = Field(max_length=12)
    scope: Literal["account", "project"]
    project_id: str | None
    expires_at: datetime | None
    created_at: datetime
