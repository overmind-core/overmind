from uuid import UUID

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel, ResourceLinkContract


class InspectOperationInput(MCPModel):
    operation: UUID
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class InspectOperationOutput(MCPModel):
    summary: str
    operation: dict
    resource: ResourceLinkContract
