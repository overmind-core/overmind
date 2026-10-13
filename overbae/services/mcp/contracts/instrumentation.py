"""Strict contracts for read-only instrumentation planning and verification."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, StrictInt, StrictStr, model_validator

from overbae.services.mcp.contracts.common import MCPModel

MAX_INSTRUMENTATION_SPANS = 100
MAX_VERIFY_TRACE_SPANS = 5_000
MAX_INSTRUMENTATION_TICKETS = 100


class InstrumentationTarget(MCPModel):
    file: str = Field(max_length=512)
    qualname: str = Field(max_length=512)
    module: str = Field(max_length=512)
    import_line: str = Field(max_length=1_024)


class InstrumentationPlacement(MCPModel):
    capability: str = Field(min_length=1, max_length=255)
    capability_id: str = Field(min_length=1, max_length=64)
    kind: Literal["entry_point", "workflow", "tool", "retrieval", "function"]
    target: InstrumentationTarget
    required_decorator: str = Field(min_length=1, max_length=255)


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


class GetInstrumentationPlanOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    capability: InstrumentationCapabilitySummary | None = None
    placements: list[InstrumentationPlacement] = Field(max_length=MAX_INSTRUMENTATION_TICKETS)
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


class InstrumentationPunchListItem(MCPModel):
    check: str = Field(min_length=1, max_length=64)
    instruction: str = Field(min_length=1, max_length=1_000)


class InstrumentationSpanRows(MCPModel):
    span_id: str = Field(max_length=16)
    name: str = Field(max_length=255)
    span_type: str = Field(max_length=40)
    rows: list[str] = Field(
        max_length=20, description="Eval matrix rows that would score this span live."
    )


class InstrumentationCapabilityResult(MCPModel):
    capability: str | None = Field(default=None, max_length=255)
    capability_id: str | None = Field(default=None, max_length=64)
    checks: dict[str, Literal["ok", "missing"]] = Field(max_length=20)
    punch_list: list[InstrumentationPunchListItem] = Field(max_length=20)
    spans: list[InstrumentationSpanRows] = Field(max_length=50)


class VerifyInstrumentationInput(MCPModel):
    capability: str | None = Field(default=None, min_length=1, max_length=255)
    trace_id: StrictStr = Field(default="", max_length=32)
    spans: list[InstrumentationSpan] = Field(
        default_factory=list, max_length=MAX_INSTRUMENTATION_SPANS
    )

    @model_validator(mode="after")
    def trace_or_spans(self) -> VerifyInstrumentationInput:
        if self.trace_id and self.spans:
            raise ValueError("pass trace_id or spans, not both")
        return self


class VerifyInstrumentationOutput(MCPModel):
    summary: str = Field(min_length=1, max_length=240)
    ok: bool
    capabilities: list[InstrumentationCapabilityResult] = Field(
        max_length=MAX_INSTRUMENTATION_SPANS
    )
    errors: list[InstrumentationSpanError] = Field(max_length=MAX_INSTRUMENTATION_SPANS)
