"""Strict, secret-free contracts for connector inspection and sync."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

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


class ConnectorHumanAction(MCPModel):
    code: str
    message: str = Field(max_length=500)
    action: str
    command: str | None = None
    resource: str | None = None


class ConnectorMappingOption(MCPModel):
    id: str
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)


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


class ImportPreview(MCPModel):
    id: str
    source_project_id: str
    window_from: datetime | None
    window_to: datetime
    status: str
    trace_count: int
    span_count: int
    estimated_seconds_min: int
    estimated_seconds_max: int
    error: str
    created_at: datetime
    finished_at: datetime | None
    expires_at: datetime | None


class TraceGroup(MCPModel):
    id: str
    name: str
    revision: int
    trace_count: int
    unreviewed_trace_count: int
    needs_review: bool
    capability_id: str | None
    mixed: bool
    evidence: dict
    sample_trace_ids: list[str]
    last_reviewed_at: datetime | None
    last_reviewed_trace_count: int | None


class ConnectorReviewSummary(MCPModel):
    pending_groups: int
    pending_traces: int


class ConnectorDetails(MCPModel):
    review_summary: ConnectorReviewSummary
    import_remaining_seconds: int | None = None
    id: str
    name: str = Field(min_length=1, max_length=255)
    connector_type: str = Field(min_length=1, max_length=40)
    status: Literal["active", "inactive"]
    verified: bool
    verified_at: datetime | None = None
    provider_capabilities: ConnectorCapabilities
    active_config: ConnectorSyncConfig | None = None
    groups: list[TraceGroup] = Field(default_factory=list)
    latest_preview: ImportPreview | None = None
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
    connector: str | None = Field(default=None, max_length=255)
    include_source_projects: bool = False
    max_runs: int = Field(default=20, ge=1, le=50)


class InspectConnectorsOutput(MCPModel):
    summary: str
    connectors: list[ConnectorDetails] = Field(default_factory=list)
    connector: ConnectorDetails | None = None
    available_types: list[AvailableConnectorType] = Field(default_factory=list)
    mapping_options: list[ConnectorMappingOption] = Field(default_factory=list)
    human_action: ConnectorHumanAction | None = None
    resource_links: list[ResourceLinkContract] = Field(default_factory=list)


class ConfigureConnectorInput(MCPModel):
    connector: str = Field(min_length=1, max_length=255)
    source_project_id: str = Field(default="", max_length=128)
    lookback_days: int | None = Field(default=None, ge=1)
    backfill_from: datetime | None = None
    backfill_to: datetime | None = None


class ConfigureConnectorOutput(MCPModel):
    summary: str
    preview: ImportPreview
    resource_links: list[ResourceLinkContract] = Field(default_factory=list)


class ConnectorJobReference(MCPModel):
    id: str
    kind: Literal["connector_sync"] = "connector_sync"
    status: Literal["queued"] = "queued"
    resource: ResourceLinkContract


class ConnectorPollHint(MCPModel):
    interval_seconds: int = Field(default=5, ge=1, le=300)
    resource: ResourceLinkContract


class SyncConnectorInput(MCPModel):
    connector: str = Field(min_length=1, max_length=255)
    preview_id: UUID | None = None


class SyncConnectorOutput(MCPModel):
    summary: str
    queued: bool
    connector: ConnectorDetails
    resource: ResourceLinkContract
    job: ConnectorJobReference | None = None
    poll_hint: ConnectorPollHint | None = None
    resource_links: list[ResourceLinkContract] = Field(default_factory=list)


class TraceGroupAssignment(MCPModel):
    group_id: UUID
    capability_id: UUID | None
    expected_revision: int = Field(ge=1)


class ReviewTraceGroupsInput(MCPModel):
    connector: str = Field(min_length=1, max_length=255)
    assignments: list[TraceGroupAssignment] = Field(min_length=1, max_length=10000)


class ReviewTraceGroupsOutput(MCPModel):
    summary: str
    groups: list[TraceGroup]
    resource_links: list[ResourceLinkContract] = Field(default_factory=list)
