"""Project-scoped, read-only instrumentation planning and verification tools."""

from __future__ import annotations

from asgiref.sync import sync_to_async

from overbae.innermind import instrumentation
from overbae.models import Span
from overbae.services.capabilities import identity
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.instrumentation import (
    MAX_INSTRUMENTATION_SPANS,
    MAX_INSTRUMENTATION_TICKETS,
    MAX_VERIFY_TRACE_SPANS,
    GetInstrumentationPlanInput,
    GetInstrumentationPlanOutput,
    InstrumentationHumanAction,
    VerifyInstrumentationInput,
    VerifyInstrumentationOutput,
)
from overbae.services.mcp.errors import MCPError

_REGISTRY_MESSAGE = "No instrumentation registry is available for this project."
_REGISTRY_INSTRUCTION = (
    "Run `/overmind setup` in the coding agent, then `overmind sync`, and request the plan again."
)
_OTLP_INSTRUCTION = (
    "There is no JavaScript/TypeScript SDK. Add Overmind's OTLP endpoint to the application's "
    "existing OpenTelemetry exporters and stamp overmind.capability.id on the resource; "
    "follow references/javascript.md."
)


def _resolve_capability(context: MCPContext, reference: str | None):
    if not reference:
        return None
    capability = identity.lookup(context.project.id, reference)
    if capability is None:
        raise MCPError("capability_not_found", "The capability was not found in this project.")
    return capability


def _plan_sync(
    payload: GetInstrumentationPlanInput, context: MCPContext
) -> GetInstrumentationPlanOutput:
    capability = _resolve_capability(context, payload.capability)
    placements = instrumentation.plan(context.project, capability)[:MAX_INSTRUMENTATION_TICKETS]
    summary = (
        {"id": str(capability.id), "name": capability.name, "slug": capability.slug}
        if capability is not None
        else None
    )
    if not placements and instrumentation.has_script_anchors(context.project, capability):
        return GetInstrumentationPlanOutput(
            summary="JavaScript/TypeScript has no decorator placements.",
            capability=summary,
            placements=[],
            instruction=_OTLP_INSTRUCTION,
        )
    if not placements:
        return GetInstrumentationPlanOutput(
            summary="Instrumentation plan is unavailable.",
            capability=summary,
            placements=[],
            human_action=InstrumentationHumanAction(
                code="instrumentation_registry_empty",
                message=_REGISTRY_MESSAGE,
                instruction=_REGISTRY_INSTRUCTION,
            ),
            instruction=_REGISTRY_INSTRUCTION,
        )
    return GetInstrumentationPlanOutput(
        summary=f"{len(placements)} placements.",
        capability=summary,
        placements=placements,
    )


def _verify_sync(
    payload: VerifyInstrumentationInput, context: MCPContext
) -> VerifyInstrumentationOutput:
    capability = _resolve_capability(context, payload.capability)
    project_id = str(context.project.id)
    if payload.trace_id:
        span_count = Span.objects.filter(project_id=project_id, trace_id=payload.trace_id).count()
        if not span_count:
            raise MCPError("resource_not_found", "The trace was not found in this project.")
        if span_count > MAX_VERIFY_TRACE_SPANS:
            raise MCPError(
                "invalid_input",
                f"The trace has {span_count} spans; verification accepts at most "
                f"{MAX_VERIFY_TRACE_SPANS}. Verify a smaller run.",
            )
        results = instrumentation.verify_trace(project_id, payload.trace_id, capability)
        errors = []
    else:
        spans, errors = instrumentation.build_spans(
            project_id,
            [span.model_dump(mode="python", exclude_none=True) for span in payload.spans],
        )
        results = instrumentation.grade(project_id, spans, capability)
    ok = bool(results) and not errors and all(not result["punch_list"] for result in results)
    return VerifyInstrumentationOutput(
        summary="Instrumentation verified." if ok else "Instrumentation gaps found.",
        ok=ok,
        capabilities=results[:MAX_INSTRUMENTATION_SPANS],
        errors=errors,
    )


def register_instrumentation_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "get_instrumentation_plan",
            "Get instrumentation plan",
            "Return where to place each Python SDK decorator for a capability, from its scanned anchors. "
            "JavaScript/TypeScript anchors get no placement; those apps export OTLP.",
            GetInstrumentationPlanInput,
            GetInstrumentationPlanOutput,
            _plan_sync,
        ),
        (
            "verify_instrumentation",
            "Verify instrumentation",
            "Check an ingested trace (trace_id) or caller-supplied spans for capability identity, "
            "a root, captured input and output, tool spans and the matrix rows that would score "
            "them. No writes.",
            VerifyInstrumentationInput,
            VerifyInstrumentationOutput,
            _verify_sync,
        ),
    ]
    for name, title, description, input_model, output_model, function in definitions:
        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=True,
                idempotent=True,
                open_world=False,
                required_scopes=frozenset({"overmind:read"}),
                cost_class="free",
                async_mode="sync",
            ),
            sync_to_async(function, thread_sensitive=True),
        )
