"""Strict, secret-free contracts for connector inspection and sync."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AliasChoices, Field, field_validator

from overbae.services.mcp.contracts.common import MCPModel, ResourceLinkContract

ConnectorMappingSource = Literal["observation_name", "metadata", "tag", "trace_name"]


class ConnectorCapabilities(MCPModel):
    exact_count: bool = False
    capability_sources: list[ConnectorMappingSource] = Field(default_factory=list, max_length=8)
    needs_source_project: bool = True
    retention_note: str = Field(default="", max_length=1_000)


class ConnectorSyncConfig(MCPModel):
    id: str
    version: int = Field(ge=1)
    source_project_id: str = Field(default="", max_length=128)
    target_project_id: str | None = None
    lookback_days: int | None = Field(default=None, ge=1, le=365)
    backfill_from: datetime | None = None
    backfill_to: datetime | None = None
    effective_from: datetime


class ConnectorCapabilityAssignment(MCPModel):
    source_value: str = Field(min_length=1, max_length=255)
    capability_id: str
    capability_name: str = Field(min_length=1, max_length=255)


class ConnectorCapabilityMapping(MCPModel):
    source: ConnectorMappingSource | None = None
    key: str | None = Field(default=None, max_length=255)
    names: list[str] = Field(default_factory=list, max_length=100)
    assignments: dict[str, str] = Field(default_factory=dict, max_length=100)
    fallback_capability_id: str | None = None


class ConnectorCapabilityMappingInput(MCPModel):
    source: ConnectorMappingSource | None = None
    key: str | None = Field(default=None, max_length=255)
    names: list[str] = Field(default_factory=list, max_length=100)
    assignments: dict[str, str] = Field(default_factory=dict, max_length=100)
    fallback_capability_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("fallback_capability_id", "fallback_capability"),
    )

    @field_validator("assignments")
    @classmethod
    def validate_assignment_values(cls, values: dict[str, str]) -> dict[str, str]:
        if any(
            not key.strip() or len(key) > 255 or len(value) > 255 for key, value in values.items()
        ):
            raise ValueError("capability mapping entries are too long or empty")
        return values


class ConnectorHumanAction(MCPModel):
    code: Literal[
        "connector_setup_required",
        "connector_config_required",
        "connector_source_required",
        "connector_mapping_approval_required",
    ]
    message: str = Field(min_length=1, max_length=500)
    action: Literal["run_cli", "configure_connector", "approve_mapping"]
    command: str | None = Field(default=None, max_length=255)
    resource: str | None = Field(default=None, max_length=512)


class ConnectorMappingOption(MCPModel):
    id: str
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)


class ConnectorObservationShape(MCPModel):
    name: str = Field(min_length=1, max_length=255)
    type: str = Field(default="", max_length=64)
    parent_name: str | None = Field(default=None, max_length=255)
    traces: int = Field(default=0, ge=0)
    score: int = 0
    reasons: list[str] = Field(default_factory=list, max_length=8)
    is_root: bool = False


class ConnectorSuggestedBoundary(MCPModel):
    name: str = Field(min_length=1, max_length=255)
    capability_id: str
    capability_name: str = Field(min_length=1, max_length=255)
    nested_names: list[str] = Field(default_factory=list, max_length=50)
    alternatives: list[str] = Field(default_factory=list, max_length=50)


class AvailableConnectorType(MCPModel):
    connector_type: str = Field(min_length=1, max_length=40)
    auth: Literal["bearer", "pair"]
    needs_source_project: bool = True
    capability_sources: list[ConnectorMappingSource] = Field(default_factory=list, max_length=8)
    command: str = Field(min_length=1, max_length=255)


class ConnectorSyncState(MCPModel):
    status: str = Field(min_length=1, max_length=32)
    auto_sync_enabled: bool
    poll_interval_seconds: int = Field(ge=60, le=86_400)
    last_synced_at: datetime | None = None
    next_poll_at: datetime | None = None
    backfill_imported: int = Field(ge=0)
    backfill_total: int | None = Field(default=None, ge=0)
    total_spans_imported: int = Field(ge=0)
    total_traces_imported: int = Field(ge=0)
    has_error: bool = False


class ConnectorSyncRun(MCPModel):
    id: str
    mode: str = Field(min_length=1, max_length=20)
    status: str = Field(min_length=1, max_length=20)
    config_version: int | None = Field(default=None, ge=1)
    traces_seen: int = Field(ge=0)
    spans_created: int = Field(ge=0)
    spans_skipped: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime | None = None
    has_error: bool = False


class ConnectorDetails(MCPModel):
    id: str
    name: str = Field(min_length=1, max_length=255)
    connector_type: str = Field(min_length=1, max_length=40)
    status: Literal["active", "inactive"]
    verified: bool
    verified_at: datetime | None = None
    provider_capabilities: ConnectorCapabilities
    active_config: ConnectorSyncConfig | None = None
    capability_mapping: ConnectorCapabilityMapping
    capability_assignments: list[ConnectorCapabilityAssignment] = Field(
        default_factory=list, max_length=100
    )
    sync: ConnectorSyncState
    sync_runs: list[ConnectorSyncRun] = Field(default_factory=list, max_length=50)
    sync_runs_truncated: bool = False
    source_projects: list[dict[str, str]] = Field(default_factory=list, max_length=50)
    source_projects_truncated: bool = False
    preview_count: int | None = Field(default=None, ge=0)
    provider_status: Literal["not_requested", "available", "unavailable", "setup_required"] = (
        "not_requested"
    )
    resource: ResourceLinkContract


class InspectConnectorsInput(MCPModel):
    connector: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("connector", "connector_id", "connector_name"),
    )
    include_source_projects: bool = False
    preview: bool = False
    preview_source_project_id: str | None = Field(default=None, max_length=128)
    preview_lookback_days: int = Field(default=30, ge=1, le=365)
    max_runs: int = Field(default=20, ge=1, le=50)


class InspectConnectorsOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    connectors: list[ConnectorDetails] = Field(default_factory=list, max_length=50)
    connector: ConnectorDetails | None = None
    available_types: list[AvailableConnectorType] = Field(default_factory=list, max_length=8)
    mapping_options: list[ConnectorMappingOption] = Field(default_factory=list, max_length=100)
    proposed_assignments: list[ConnectorCapabilityAssignment] = Field(
        default_factory=list, max_length=100
    )
    observation_shapes: list[ConnectorObservationShape] = Field(default_factory=list, max_length=50)
    suggested_boundaries: list[ConnectorSuggestedBoundary] = Field(
        default_factory=list, max_length=50
    )
    unmapped_roots: list[str] = Field(default_factory=list, max_length=50)
    console_traces_url: str = Field(default="", max_length=512)
    human_action: ConnectorHumanAction | None = None
    resource_links: list[ResourceLinkContract] = Field(default_factory=list, max_length=51)


class ConfigureConnectorInput(MCPModel):
    connector: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("connector", "connector_id", "connector_name"),
    )
    source_project_id: str | None = Field(default=None, max_length=128)
    lookback_days: int | None = Field(default=None, ge=1, le=365)
    backfill_from: datetime | None = None
    backfill_to: datetime | None = None
    capability_mapping: ConnectorCapabilityMappingInput | None = Field(
        default=None,
        validation_alias=AliasChoices("capability_mapping", "mapping"),
    )
    auto_sync_enabled: bool | None = None
    poll_interval_seconds: int | None = Field(default=None, ge=60, le=86_400)
    confirm_mapping: bool = False

    @field_validator("backfill_to")
    @classmethod
    def validate_backfill_range(cls, value: datetime | None, info):
        start = info.data.get("backfill_from")
        if value is not None and start is not None and start >= value:
            raise ValueError("backfill_from must be before backfill_to")
        return value


class ConfigureConnectorOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    configured: bool
    mapping_pending: bool = False
    proposed_assignments: list[ConnectorCapabilityAssignment] = Field(
        default_factory=list, max_length=100
    )
    mapping_options: list[ConnectorMappingOption] = Field(default_factory=list, max_length=100)
    observation_shapes: list[ConnectorObservationShape] = Field(default_factory=list, max_length=50)
    suggested_boundaries: list[ConnectorSuggestedBoundary] = Field(
        default_factory=list, max_length=50
    )
    unmapped_roots: list[str] = Field(default_factory=list, max_length=50)
    dropped_nested_names: list[str] = Field(default_factory=list, max_length=50)
    console_traces_url: str = Field(default="", max_length=512)
    connector: ConnectorDetails
    human_action: ConnectorHumanAction | None = None
    resource: ResourceLinkContract
    resource_links: list[ResourceLinkContract] = Field(max_length=2)


class ConnectorJobReference(MCPModel):
    id: str
    kind: Literal["connector_sync"] = "connector_sync"
    status: Literal["queued"] = "queued"
    resource: ResourceLinkContract


class ConnectorPollHint(MCPModel):
    interval_seconds: int = Field(default=5, ge=1, le=300)
    resource: ResourceLinkContract


class SyncConnectorInput(MCPModel):
    connector: str = Field(
        min_length=1,
        max_length=255,
        validation_alias=AliasChoices("connector", "connector_id", "connector_name"),
    )


class SyncConnectorOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    queued: bool
    recarving: bool = False
    console_traces_url: str = Field(default="", max_length=512)
    connector: ConnectorDetails
    resource: ResourceLinkContract
    job: ConnectorJobReference | None = None
    poll_hint: ConnectorPollHint | None = None
    human_action: ConnectorHumanAction | None = None
    resource_links: list[ResourceLinkContract] = Field(max_length=2)
