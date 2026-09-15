from __future__ import annotations

import asyncio

import pytest
from django.test import override_settings

from overbae.models import APIToken, Project, User
from overbae.services.finetuning_catalog import fetch_finetuning_model_catalog
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.model_catalog import GetModelCatalogOutput


def _context(permission: str = "read") -> MCPContext:
    return MCPContext(
        user=User(email="mcp-catalog@example.com"),
        token=APIToken(scope={"scope": "project", "permission": [permission]}),
        project=Project(name="MCP catalog", slug="mcp-catalog"),
    )


def _call(arguments: dict, *, permission: str = "read"):
    return asyncio.run(CATALOG.call("get_model_catalog", arguments, _context(permission)))


def test_catalog_metadata_and_input_schema_are_read_only_and_bounded():
    definition = CATALOG.get("get_model_catalog").definition
    assert definition.read_only is True
    assert definition.destructive is False
    assert definition.idempotent is True
    assert definition.open_world is False
    assert definition.required_scopes == frozenset({"overmind:read"})
    assert definition.cost_class == "free"
    assert definition.async_mode == "sync"

    schema = definition.as_mcp_tool().inputSchema
    assert set(schema["properties"]) == {"has_tool_calling", "max_context"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["max_context"]["maximum"] == 10_000_000
    assert schema["properties"]["max_context"]["exclusiveMinimum"] == 0
    assert {tool.name for tool in CATALOG.tools(frozenset({"write"}))}.isdisjoint(
        {"get_model_catalog"}
    )


def test_catalog_is_dataset_independent_and_returns_complete_model_metadata():
    with override_settings(FINETUNING_BACKEND="modal"):
        result = _call({})

    assert result.isError is False
    output = result.structuredContent
    assert set(output) == {
        "summary",
        "backend",
        "tiers",
        "models",
        "has_tool_calling",
        "max_context",
    }
    assert output["backend"] == "modal"
    assert output["has_tool_calling"] is False
    assert output["max_context"] is None
    assert output["summary"] == (
        f"{sum(len(models) for models in output['models'].values())} "
        "fine-tuning catalog entries returned for modal."
    )
    assert len(output["summary"]) <= 240

    required = {
        "id",
        "display",
        "params",
        "total_params_b",
        "context_length_sft",
        "context_length",
        "min_batch_size",
        "max_batch_size",
        "supports_tool_calling",
        "training_type",
    }
    models = [model for tier in output["models"].values() for model in tier]
    assert models
    assert any(model.get("disabled") for model in models)
    for model in models:
        assert required <= set(model)
        assert set(model["training_type"]) == {"lora", "full"}
        for method in model["training_type"].values():
            assert set(method) == {
                "enabled",
                "context_length",
                "validated_context_length",
            }
        assert model["min_batch_size"] <= model["max_batch_size"]

    training_schema = GetModelCatalogOutput.model_json_schema()["$defs"]["ModelCatalogTrainingType"]
    assert set(training_schema["properties"]) == {"lora", "full"}
    assert training_schema["required"] == ["lora", "full"]
    assert training_schema["additionalProperties"] is False


def test_catalog_filters_tool_calling_and_preserves_together_context_semantics():
    with override_settings(FINETUNING_BACKEND="baseten"):
        tool_result = _call({"has_tool_calling": True})
    assert tool_result.isError is False
    tool_models = [
        model for tier in tool_result.structuredContent["models"].values() for model in tier
    ]
    assert tool_models
    assert all(
        model["supports_tool_calling"] and not model.get("disabled") for model in tool_models
    )

    with override_settings(FINETUNING_BACKEND="together"):
        result = _call({"max_context": 50_000})
        expected = fetch_finetuning_model_catalog(max_context=50_000)

    assert result.isError is False
    assert result.structuredContent["models"] == expected["models"]
    assert result.structuredContent["max_context"] == 50_000
    assert all(
        "context_length" not in model
        for tier in result.structuredContent["models"].values()
        for model in tier
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {"has_tool_calling": "true"},
        {"has_tool_calling": 1},
        {"max_context": "50000"},
        {"max_context": 50000.0},
    ],
)
def test_catalog_rejects_coercible_input_types(arguments):
    result = _call(arguments)

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "invalid_input"


def test_catalog_requires_read_permission():
    result = _call({}, permission="write")

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "permission_denied"
