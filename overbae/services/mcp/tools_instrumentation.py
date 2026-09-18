"""Project-scoped, read-only instrumentation planning and verification tools."""

from __future__ import annotations

from asgiref.sync import sync_to_async

from overbae.services.behaviour import dry_run
from overbae.services.behaviour.instrumentation import instrumentation_tickets
from overbae.services.capabilities import identity
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.instrumentation import (
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
    result = instrumentation_tickets(
        context.project,
        capability,
        payload.behaviour or "",
    )
    if result.get("error"):
        if payload.behaviour:
            raise MCPError(
                "instrumentation_plan_not_found",
                "The requested instrumentation behaviour was not found in this project.",
            )
        return GetInstrumentationPlanOutput(
            summary="Instrumentation plan is unavailable.",
            capability=result.get("capability"),
            placements=[],
            human_action=InstrumentationHumanAction(
                code="instrumentation_registry_empty",
                message=_REGISTRY_MESSAGE,
                instruction=_REGISTRY_INSTRUCTION,
            ),
            instruction=_REGISTRY_INSTRUCTION,
        )
    return GetInstrumentationPlanOutput(
        summary="Instrumentation plan returned.",
        capability=result.get("capability"),
        placements=result.get("placements") or [],
    )


def _verify_sync(
    payload: VerifyInstrumentationInput, context: MCPContext
) -> VerifyInstrumentationOutput:
    capability = _resolve_capability(context, payload.capability)
    result = dry_run.verify_spans(
        str(context.project.id),
        [span.model_dump(mode="python", exclude_none=True) for span in payload.spans],
        capability=capability,
    )
    return VerifyInstrumentationOutput(
        summary="Instrumentation verified." if result.get("ok") else "Instrumentation gaps found.",
        ok=bool(result.get("ok")),
        tasks=result.get("tasks") or [],
        capabilities=result.get("capabilities") or [],
        errors=result.get("errors") or [],
    )


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_instrumentation_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "get_instrumentation_plan",
            "Get instrumentation plan",
            "Return exact registered instrumentation tickets for a project capability or behaviour.",
            GetInstrumentationPlanInput,
            GetInstrumentationPlanOutput,
            _plan_sync,
        ),
        (
            "verify_instrumentation",
            "Verify instrumentation",
            "Bind and grade a bounded caller-supplied span list without writing traces or scores.",
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
            _async_handler(function),
        )
