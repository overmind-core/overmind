"""Strict contracts for decision jobs."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel, ResourceLinkContract


class StartDecisionJobInput(MCPModel):
    model: str
    dataset: str
    eval_dataset: str | None = None


class DecisionJobContract(MCPModel):
    id: str
    model: str
    status: str
    dataset: str
    version: str
    eval_dataset: str | None = None
    eval_version: str | None = None
    error: str | None = None
    evaluation: dict[str, Any] = Field(default_factory=dict)
    cost_usd: str | None = None
    resource: ResourceLinkContract


class StartDecisionJobOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    job: DecisionJobContract
    resource_links: list[ResourceLinkContract] = Field(max_length=2)
