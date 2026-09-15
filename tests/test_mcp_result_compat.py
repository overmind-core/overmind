from __future__ import annotations

import json

import pytest
from mcp import types
from pydantic import BaseModel, Field

from overbae.models import APIToken, Project, User
from overbae.services.mcp.catalog import ToolCatalog, ToolDefinition
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.errors import MCPError, error_result


class Input(BaseModel):
    value: str


class Output(BaseModel):
    value: str
    resource: dict | None = None
    resource_links: list[dict] = Field(default_factory=list)


def _context() -> MCPContext:
    return MCPContext(
        user=User(email="mcp-result@example.com"),
        token=APIToken(scope={"scope": "project", "permission": ["read"]}),
        project=Project(name="MCP", slug="mcp-result"),
    )


def _catalog() -> ToolCatalog:
    definition = ToolDefinition(
        name="read_state",
        title="Read state",
        description="Read state.",
        input_model=Input,
        output_model=Output,
        read_only=True,
        idempotent=True,
        open_world=False,
        required_scopes=frozenset({"overmind:read"}),
        cost_class="free",
        async_mode="sync",
    )
    catalog = ToolCatalog()
    catalog.register(
        definition,
        lambda payload, _context: Output(
            value=payload.value,
            resource={
                "uri": "overmind://deployments/one",
                "title": "Deployment one",
                "mimeType": "application/json",
            },
            resource_links=[
                {
                    "uri": "overmind://deployments/one",
                    "title": "Duplicate deployment",
                    "mimeType": "application/json",
                },
                {
                    "uri": "overmind://datasets/one",
                    "title": "Dataset one",
                    "mimeType": "application/json",
                },
                {
                    "uri": "overmind://datasets/one",
                    "title": "Duplicate",
                    "mimeType": "application/json",
                },
                {
                    "uri": "not-a-resource",
                    "title": "Invalid",
                    "mimeType": "application/json",
                },
            ],
        ),
    )
    return catalog


@pytest.mark.asyncio
async def test_success_result_keeps_structured_payload_and_exports_unique_resource_links():
    result = await _catalog().call("read_state", {"value": "ok"}, _context())

    assert result.isError is False
    assert result.content[0].type == "text"
    assert result.content[0].text == json.dumps(result.structuredContent, separators=(",", ":"))
    assert result.structuredContent == {
        "value": "ok",
        "resource": {
            "uri": "overmind://deployments/one",
            "title": "Deployment one",
            "mimeType": "application/json",
        },
        "resource_links": [
            {
                "uri": "overmind://deployments/one",
                "title": "Duplicate deployment",
                "mimeType": "application/json",
            },
            {
                "uri": "overmind://datasets/one",
                "title": "Dataset one",
                "mimeType": "application/json",
            },
            {
                "uri": "overmind://datasets/one",
                "title": "Duplicate",
                "mimeType": "application/json",
            },
            {
                "uri": "not-a-resource",
                "title": "Invalid",
                "mimeType": "application/json",
            },
        ],
    }
    assert len(result.content) == 3
    link = result.content[1]
    assert isinstance(link, types.ResourceLink)
    assert link.name == "overmind://deployments/one"
    assert str(link.uri) == "overmind://deployments/one"
    assert link.title == "Deployment one"
    assert link.mimeType == "application/json"
    assert str(result.content[2].uri) == "overmind://datasets/one"


def test_error_result_uses_the_same_json_compatibility_content():
    result = error_result(MCPError("invalid_input", "Bad input."))

    assert result.isError is True
    assert result.content[0].type == "text"
    assert result.content[0].text == json.dumps(result.structuredContent, separators=(",", ":"))
    assert json.loads(result.content[0].text) == result.structuredContent
    assert len(result.content) == 1
