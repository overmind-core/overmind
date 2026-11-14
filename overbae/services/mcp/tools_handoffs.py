"""Project-scoped MCP tools for work the Console assistant hands to a coding agent."""

from __future__ import annotations

from overbae.services.assistant import handoffs
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.handoffs import (
    CompleteHandoffInput,
    CompleteHandoffOutput,
    HandoffContract,
    ListHandoffsInput,
    ListHandoffsOutput,
)
from overbae.services.mcp.tools_model_workflows import handler


def _list_sync(_payload: ListHandoffsInput, context: MCPContext) -> ListHandoffsOutput:
    found, total = handoffs.waiting(context.project)
    return ListHandoffsOutput(
        summary=f"{total} open handoffs, {len(found)} listed, oldest first.",
        handoffs=[
            HandoffContract(
                id=str(handoff.pk),
                state=handoff.state,
                instructions=handoff.instructions,
                created_at=handoff.created_at.isoformat(),
            )
            for handoff in found
        ],
    )


def _complete_sync(payload: CompleteHandoffInput, context: MCPContext) -> CompleteHandoffOutput:
    handoff = handoffs.complete(context.project, payload.handoff, payload.result)
    return CompleteHandoffOutput(
        summary="The assistant has the report.",
        handoff=HandoffContract(id=str(handoff.pk), state=handoff.state),
    )


def register_handoff_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition  # lazy: the catalog imports this

    catalog.register(
        ToolDefinition(
            name="list_assistant_handoffs",
            title="List assistant handoffs",
            description="List work the Console assistant left for you because it needs this "
            "repository or machine. The user approved each one in the Console. Do each one "
            "as its instructions say, under your own permission rules, then report with "
            "complete_assistant_handoff; the assistant's conversation waits for the report.",
            input_model=ListHandoffsInput,
            output_model=ListHandoffsOutput,
            read_only=True,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:read"}),
            cost_class="free",
            async_mode="sync",
        ),
        handler(_list_sync),
    )
    catalog.register(
        ToolDefinition(
            name="complete_assistant_handoff",
            title="Complete an assistant handoff",
            description="Report the result of one handoff from list_assistant_handoffs. State "
            "what you did and measured, with exact Overmind ids. The Console assistant then "
            "continues its conversation with this report.",
            input_model=CompleteHandoffInput,
            output_model=CompleteHandoffOutput,
            read_only=False,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:data:write"}),
            cost_class="free",
            async_mode="sync",
        ),
        handler(_complete_sync),
    )
