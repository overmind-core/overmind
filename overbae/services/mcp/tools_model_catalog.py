"""Dataset-independent model catalog MCP tool: chat fine-tuning and decision models."""

from __future__ import annotations

from overbae.modal.decision_catalog import models as decision_catalog
from overbae.services.finetuning_catalog import fetch_finetuning_model_catalog
from overbae.services.mcp.contracts.model_catalog import (
    DecisionCatalogModel,
    GetModelCatalogInput,
    GetModelCatalogOutput,
)


def _get_model_catalog(payload, _context):
    catalog = fetch_finetuning_model_catalog(
        has_tool_calling=payload.has_tool_calling,
        max_context=payload.max_context,
    )
    count = sum(len(models) for models in catalog["models"].values())
    decision_models = [
        DecisionCatalogModel(
            id=spec["id"],
            family=spec["family"],
            params=spec["params"],
            max_context_length=spec["max_context_length"],
            limits=spec["limits"],
            decision_index=spec.get("decision_index"),
        )
        for spec in decision_catalog().values()
    ]
    return GetModelCatalogOutput(
        summary=(
            f"{count} fine-tuning catalog entries and {len(decision_models)} decision models "
            f"returned for {catalog['backend']}."
        ),
        decision_models=decision_models,
        **catalog,
    )


def register_model_catalog_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    catalog.register(
        ToolDefinition(
            name="get_model_catalog",
            title="Get model catalog",
            description=(
                "List chat fine-tuning models (tiers, context limits, training methods, "
                "tool-calling, batch bounds) and decision models (family, size, serving "
                "limits, Decision Index)."
            ),
            input_model=GetModelCatalogInput,
            output_model=GetModelCatalogOutput,
            read_only=True,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:read"}),
            cost_class="free",
            async_mode="sync",
            project_scoped=False,
        ),
        _get_model_catalog,
    )
