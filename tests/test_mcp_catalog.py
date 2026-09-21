from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from overbae.models import APIToken, Project, User
from overbae.services.mcp.catalog import CatalogError, ToolCatalog, ToolDefinition
from overbae.services.mcp.context import MCPContext


class ReadInput(BaseModel):
    query: str


class ReadOutput(BaseModel):
    answer: str


def _definition(**overrides) -> ToolDefinition:
    values = {
        "name": "read_state",
        "title": "Read state",
        "description": "Read a bounded project state value.",
        "input_model": ReadInput,
        "output_model": ReadOutput,
        "read_only": True,
        "destructive": False,
        "idempotent": True,
        "open_world": False,
        "required_scopes": frozenset({"overmind:read"}),
        "cost_class": "free",
        "async_mode": "sync",
    }
    values.update(overrides)
    return ToolDefinition(**values)


def _context(permission: str) -> MCPContext:
    user = User(email="mcp-catalog@example.com")
    project = Project(name="MCP", slug="mcp-catalog")
    token = APIToken(scope={"scope": "project", "permission": [permission]})
    return MCPContext(user=user, token=token, project=project)


def test_catalog_emits_typed_schemas_and_annotations():
    definition = _definition()
    tool = definition.as_mcp_tool()

    assert tool.inputSchema["type"] == "object"
    assert "title" not in tool.inputSchema
    assert tool.description == "Read a bounded project state value."
    assert tool.outputSchema is None
    assert tool.annotations.readOnlyHint is True
    assert tool.annotations.destructiveHint is False
    assert tool.annotations.idempotentHint is True
    assert tool.annotations.openWorldHint is False


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": "ping"},
        {"name": "delete_dataset"},
        {"name": "retry_finetune"},
        {"destructive": True},
        {"required_scopes": frozenset({"unknown"})},
        {"required_scopes": frozenset()},
    ],
)
def test_catalog_rejects_invalid_definitions(overrides):
    with pytest.raises((CatalogError, ValueError)):
        _definition(**overrides)


@pytest.mark.asyncio
async def test_catalog_returns_structured_content_and_enforces_permission():
    async def handler(payload, _context):
        return ReadOutput(answer=payload.query)

    catalog = ToolCatalog()
    catalog.register(_definition(), handler)

    result = await catalog.call("read_state", {"query": "ok"}, _context("read"))
    assert result.isError is False
    assert result.structuredContent == {"answer": "ok"}
    assert json.loads(result.content[0].text) == result.structuredContent

    denied = await catalog.call("read_state", {"query": "ok"}, _context("write"))
    assert denied.isError is True
    assert denied.structuredContent["error"]["code"] == "permission_denied"


@pytest.mark.asyncio
async def test_catalog_validates_handler_output_before_returning_structured_content():
    async def handler(_payload, _context):
        return {}

    catalog = ToolCatalog()
    catalog.register(_definition(), handler)

    result = await catalog.call("read_state", {"query": "ok"}, _context("read"))

    assert result.isError is True
    assert result.structuredContent["error"]["code"] == "invalid_output"


def test_catalog_never_exposes_delete_or_protocol_tools():
    catalog = ToolCatalog()
    catalog.register(_definition(), lambda *_: ReadOutput(answer="ok"))
    names = {tool.name for tool in catalog.tools(frozenset({"read"}))}
    assert names == {"read_state"}


def test_catalog_allows_only_the_curated_retry_tool():
    assert _definition(name="retry_deployment").name == "retry_deployment"


@pytest.mark.asyncio
async def test_unexpected_failure_is_logged_but_not_exposed_to_the_client(caplog):
    def handler(_payload, _context):
        raise RuntimeError("private database diagnostic")

    catalog = ToolCatalog()
    catalog.register(_definition(), handler)
    result = await catalog.call("read_state", {"query": "user data"}, _context("read"))
    assert result.structuredContent["error"]["code"] == "internal_error"
    assert "private database diagnostic" not in result.content[0].text
    assert "MCP tool read_state failed" in caplog.text
    assert "private database diagnostic" in caplog.text
    assert "user data" not in caplog.text
