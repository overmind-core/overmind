"""Strict contracts for the assistant handoff MCP tools."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from overbae.services.assistant.handoffs import LISTED, MAX_CHARS
from overbae.services.mcp.contracts.common import MCPModel


class ListHandoffsInput(MCPModel):
    pass


class HandoffContract(MCPModel):
    id: str
    state: str
    instructions: str = Field(default="", max_length=MAX_CHARS)
    created_at: str = ""


class ListHandoffsOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    handoffs: list[HandoffContract] = Field(max_length=LISTED)


class CompleteHandoffInput(MCPModel):
    handoff: UUID
    result: str = Field(
        min_length=1,
        max_length=MAX_CHARS,
        description="What you did and measured, with the Overmind ids the assistant needs.",
    )


class CompleteHandoffOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    handoff: HandoffContract
