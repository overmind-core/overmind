from __future__ import annotations

import json

from mcp_fixtures import EXPECTED_TOOL_NAMES

from overbae.services.mcp.catalog import CATALOG

MAX_MANIFEST_BYTES = 34 * 1024
SECRET_INPUT_PARTS = {
    "access_token",
    "api_key",
    "authorization",
    "cookie",
    "credential",
    "password",
    "private_key",
    "refresh_token",
    "secret",
    "token",
}
FORBIDDEN_NAME_PARTS = ("delete", "remove", "cancel", "retry", "undeploy")
ALLOWED_LIFECYCLE_TOOLS = {"retry_deployment"}


def _serialized_tools(permissions: frozenset[str]) -> bytes:
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "result": {
            "tools": [
                tool.model_dump(mode="json", exclude_none=True)
                for tool in CATALOG.tools(permissions)
            ]
        },
    }
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


def _property_names(value):
    if isinstance(value, dict):
        for key, child in value.get("properties", {}).items():
            yield key
            yield from _property_names(child)
        for child in value.get("$defs", {}).values():
            yield from _property_names(child)
        for key, child in value.items():
            if key not in {"properties", "$defs"}:
                yield from _property_names(child)
    elif isinstance(value, list):
        for child in value:
            yield from _property_names(child)


def test_read_only_and_read_write_manifests_are_filtered():
    read_names = {tool.name for tool in CATALOG.tools(frozenset({"read"}))}
    full_names = {tool.name for tool in CATALOG.tools(frozenset({"read", "write"}))}
    read_definition_names = {
        definition.name for definition in CATALOG.definitions() if definition.read_only
    }

    assert read_names == read_definition_names
    assert full_names == EXPECTED_TOOL_NAMES


def test_manifest_is_compact_and_omits_optional_output_schemas():
    tools = CATALOG.tools(frozenset({"read", "write"}))
    definitions = {definition.name: definition for definition in CATALOG.definitions()}

    assert len(_serialized_tools(frozenset({"read", "write"}))) <= MAX_MANIFEST_BYTES
    assert all(tool.outputSchema is None for tool in tools)
    assert all(tool.description == definitions[tool.name].description for tool in tools)


def test_dataset_upload_resource_is_listed_and_static():
    from overbae.services.mcp.resources import resource_list

    resource = next(item for item in resource_list() if item.name == "dataset-upload")
    assert str(resource.uri) == "overmind://dataset-upload"


def test_input_schemas_keep_strict_validation_without_secret_fields():
    for definition in CATALOG.definitions():
        schema = definition.as_mcp_tool().inputSchema
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert "title" not in json.dumps(schema)
        assert '"default":' not in json.dumps(schema)
        for name in _property_names(schema):
            parts = name.casefold().replace("-", "_").split("_")
            assert not SECRET_INPUT_PARTS.intersection(parts), (definition.name, name)


def test_manifest_annotations_cover_read_only_world_and_cost_metadata():
    expected = {
        "run_evaluation": (False, False, True, "llm", "job"),
        "start_finetune": (False, False, True, "gpu", "job"),
        "retry_deployment": (False, False, True, "gpu", "job"),
        "run_inference": (False, False, True, "llm", "sync"),
        "get_model_swap_prompt": (True, True, False, "free", "sync"),
        "inspect_connectors": (True, True, True, "compute", "sync"),
        "configure_connector": (False, False, False, "free", "sync"),
        "sync_connector": (False, False, True, "free", "task"),
        "get_model_catalog": (True, True, False, "free", "sync"),
        "set_benchmark_model": (False, True, False, "free", "sync"),
    }
    definitions = {definition.name: definition for definition in CATALOG.definitions()}

    assert all(not definition.destructive for definition in definitions.values())
    assert all(
        definition.name in ALLOWED_LIFECYCLE_TOOLS
        or not any(part in definition.name for part in FORBIDDEN_NAME_PARTS)
        for definition in definitions.values()
    )
    for name, (read_only, idempotent, open_world, cost_class, async_mode) in expected.items():
        definition = definitions[name]
        assert (
            definition.read_only,
            definition.idempotent,
            definition.open_world,
            definition.cost_class,
            definition.async_mode,
        ) == (read_only, idempotent, open_world, cost_class, async_mode)

        tool = definition.as_mcp_tool()
        assert tool.annotations.readOnlyHint is read_only
        assert tool.annotations.destructiveHint is False
        assert tool.annotations.idempotentHint is idempotent
        assert tool.annotations.openWorldHint is open_world
