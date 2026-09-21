"""Common Pydantic configuration for MCP contracts."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MCPModel(BaseModel):
    """Strict JSON models used at the MCP trust boundary."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class ResourceLinkContract(MCPModel):
    uri: str = Field(pattern=r"^overmind://[^\s]+$", max_length=512)
    title: str = Field(min_length=1, max_length=160)
    mime_type: Literal["application/json"] = Field("application/json", alias="mimeType")


class JobReceipt(MCPModel):
    kind: str = Field(min_length=1, max_length=64)
    id: str
    status: str
    resource: ResourceLinkContract


class DatasetCellContract(MCPModel):
    id: str
    version: str = Field(default="", max_length=32)
    title: str = Field(default="", max_length=255)
    rows: int = Field(ge=0)
    fingerprint: str = Field(default="", max_length=64)
    fits: bool = False
    reason: str = Field(default="", max_length=500)
    warnings: list[str] = Field(default_factory=list)


class PageContract(MCPModel):
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    total: int = Field(ge=0)
    has_more: bool
    next_cursor: str | None = Field(default=None, max_length=128)
