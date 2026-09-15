"""Dataset-independent fine-tuning model catalog MCP tool."""

from __future__ import annotations

from overbae.services.finetuning_catalog import fetch_finetuning_model_catalog
from overbae.services.mcp.contracts.model_catalog import (
    GetModelCatalogInput,
    GetModelCatalogOutput,
)


def _get_model_catalog(payload, _context):
    catalog = fetch_finetuning_model_catalog(
        has_tool_calling=payload.has_tool_calling,
        max_context=payload.max_context,
    )
    count = sum(len(models) for models in catalog["models"].values())
    return GetModelCatalogOutput(
        summary=f"{count} fine-tuning catalog entries returned for {catalog['backend']}.",
        **catalog,
    )


def register_model_catalog_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    catalog.register(
        ToolDefinition(
            name="get_model_catalog",
            title="Get fine-tuning model catalog",
            description=(
                "List models available for fine-tuning, including tiers, context limits, "
                "training methods, tool-calling support, and batch bounds."
            ),
            input_model=GetModelCatalogInput,
            output_model=GetModelCatalogOutput,
            read_only=True,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:read"}),
            cost_class="free",
            async_mode="sync",
        ),
        _get_model_catalog,
    )
