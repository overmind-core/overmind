"""Strict contracts for the signal MCP tools."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel, ResourceLinkContract

MAX_SIGNALS = 50


class ListSignalsInput(MCPModel):
    capability: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Include this capability's subscriptions alongside the project-wide ones.",
    )


class SignalSubscriptionContract(MCPModel):
    id: str
    capability: str | None = None
    enabled: bool
    config: dict[str, Any] = Field(default_factory=dict)


class SignalCatalogEntryContract(MCPModel):
    signal_type: str
    description: str
    params: dict[str, Any] = Field(default_factory=dict)
    default_enabled: bool
    enabled: bool
    subscriptions: list[SignalSubscriptionContract] = Field(default_factory=list, max_length=20)


class ListSignalsOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    signals: list[SignalCatalogEntryContract] = Field(max_length=MAX_SIGNALS)
    unseen_events: int = Field(ge=0)
    resource_links: list[ResourceLinkContract] = Field(max_length=1)


class SetSignalSubscriptionInput(MCPModel):
    signal_type: str = Field(min_length=1, max_length=64)
    capability: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Watch one capability; omit to watch every capability in the project.",
    )
    enabled: bool = True
    config: dict[str, Any] = Field(
        default_factory=dict,
        max_length=10,
        description='Overrides of the signal\'s params, e.g. {"times": 5}.',
    )


class SetSignalSubscriptionOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    status: str
    subscription: SignalSubscriptionContract
    signal_type: str
    resource_links: list[ResourceLinkContract] = Field(max_length=1)
