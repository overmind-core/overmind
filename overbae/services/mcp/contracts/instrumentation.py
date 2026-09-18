"""Strict contracts for read-only instrumentation planning and verification."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from overbae.services.mcp.contracts.common import MCPModel

MAX_INSTRUMENTATION_SPANS = 100
MAX_INSTRUMENTATION_TICKETS = 100


class InstrumentationTarget(MCPModel):
    file: str = Field(max_length=512)
    qualname: str = Field(max_length=512)
    module: str = Field(max_length=512)
    import_line: str = Field(max_length=1_024)


class RequiredInstrumentationSpan(MCPModel):
    target: InstrumentationTarget
    required_decorator: str = Field(min_length=1, max_length=255)


class RequiredInstrumentationIdentity(MCPModel):
    capability_id: str = Field(min_length=1, max_length=64)
    capability_name: str = Field(min_length=1, max_length=255)
    how: str = Field(min_length=1, max_length=1_000)


class InstrumentationTicket(MCPModel):
    key: str = Field(min_length=1, max_length=255)
    behaviour_id: str = Field(min_length=1, max_length=64)
    version_id: str | None = Field(default=None, min_length=1, max_length=64)
    version_analyzed_sha: str = Field(max_length=128)
    contract_fingerprint: str = Field(max_length=128)
    capability: str = Field(min_length=1, max_length=255)
    capability_id: str = Field(min_length=1, max_length=64)
    placement_mode: Literal["fixed", "dynamic_key"]
    allowed_keys: list[str] = Field(default_factory=list, max_length=100)
    grain: Literal["run", "turn"]
    target: InstrumentationTarget
    required_scope: str = Field(min_length=1, max_length=1_000)
    required_spans: list[RequiredInstrumentationSpan] = Field(max_length=100)
    required_identity: RequiredInstrumentationIdentity


class InstrumentationCapabilitySummary(MCPModel):
    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    slug: str = Field(min_length=1, max_length=255)


class InstrumentationHumanAction(MCPModel):
    code: Literal["instrumentation_registry_empty"]
    message: str = Field(min_length=1, max_length=500)
    instruction: str = Field(min_length=1, max_length=1_000)


class GetInstrumentationPlanInput(MCPModel):
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    behaviour: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def behaviour_requires_capability(self) -> GetInstrumentationPlanInput:
        if self.behaviour and not self.capability:
            raise ValueError("behaviour requires capability")
        return self


class GetInstrumentationPlanOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    capability: InstrumentationCapabilitySummary | None = None
    placements: list[InstrumentationTicket] = Field(max_length=MAX_INSTRUMENTATION_TICKETS)
    human_action: InstrumentationHumanAction | None = None
    instruction: str | None = Field(default=None, max_length=1_000)


class InstrumentationSpan(MCPModel):
    span_id: StrictStr | None = Field(default=None, min_length=1, max_length=16)
    trace_id: StrictStr | None = Field(default=None, min_length=1, max_length=32)
    parent_span_id: StrictStr | None = Field(default=None, max_length=16)
    span_type: StrictStr | None = Field(default=None, max_length=40)
    name: StrictStr | None = Field(default=None, max_length=255)
    start_time_ns: StrictInt | None = Field(default=None, ge=0)
    end_time_ns: StrictInt | None = Field(default=None, ge=0)
    attributes: dict[str, Any] | None = Field(default=None, max_length=100)
    events: list[dict[str, Any]] | None = Field(default=None, max_length=100)
    resource_attrs: dict[str, Any] | None = Field(default=None, max_length=100)
    status_code: StrictInt | None = Field(default=None, ge=0, le=2)


class InstrumentationSpanError(MCPModel):
    index: int = Field(ge=0, le=MAX_INSTRUMENTATION_SPANS - 1)
    error: str = Field(min_length=1, max_length=500)


class InstrumentationSeenSpan(MCPModel):
    name: str = Field(max_length=255)
    behaviour_key: str | None = Field(default=None, max_length=255)
    unit_kind: str | None = Field(default=None, max_length=32)
    qualname: str | None = Field(default=None, max_length=512)


class InstrumentationTask(MCPModel):
    behaviour_key: str | None = Field(default=None, max_length=255)
    binding_source: Literal["anchor_join", "declared", "unbound"]
    declared_key: str | None = Field(default=None, max_length=255)
    route_flags: list[str] = Field(max_length=20)
    unit_span_id: str = Field(min_length=1, max_length=16)
    trace_id: str = Field(min_length=1, max_length=32)
    capability: str | None = Field(default=None, max_length=255)
    capability_id: str | None = Field(default=None, max_length=64)
    spans_seen: list[InstrumentationSeenSpan] = Field(max_length=20)


class InstrumentationPunchListItem(MCPModel):
    grade: str = Field(min_length=1, max_length=64)
    instruction: str = Field(min_length=1, max_length=1_000)


class InstrumentationCapabilityResult(MCPModel):
    capability: str | None = Field(default=None, max_length=255)
    capability_id: str | None = Field(default=None, max_length=64)
    grades: dict[str, str] = Field(max_length=20)
    punch_list: list[InstrumentationPunchListItem] = Field(max_length=20)


class VerifyInstrumentationInput(MCPModel):
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    spans: list[InstrumentationSpan] = Field(max_length=MAX_INSTRUMENTATION_SPANS)


class VerifyInstrumentationOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    ok: bool
    tasks: list[InstrumentationTask] = Field(max_length=MAX_INSTRUMENTATION_SPANS)
    capabilities: list[InstrumentationCapabilityResult] = Field(
        max_length=MAX_INSTRUMENTATION_SPANS
    )
    errors: list[InstrumentationSpanError] = Field(max_length=MAX_INSTRUMENTATION_SPANS)
