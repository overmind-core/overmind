"""Curated MCP tool catalog with typed schemas and safe invocation."""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Literal

from mcp import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from overbae.services.mcp.annotations import annotations_for
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.errors import MCPError, error_result, internal_error
from overbae.services.mcp.result_compat import tool_result

logger = logging.getLogger(__name__)

CostClass = Literal["free", "compute", "llm", "gpu"]
AsyncMode = Literal["sync", "job", "task"]
_TOOL_NAME = r"^[a-z][a-z0-9_]{0,63}$"
_FORBIDDEN_NAME_PARTS = ("delete", "remove", "cancel", "retry", "undeploy")
_ALLOWED_LIFECYCLE_TOOLS = frozenset({"retry_deployment"})
_SCHEMA_NOISE_KEYS = frozenset({"default", "discriminator", "title"})
_KNOWN_SCOPES = frozenset(
    {
        "overmind:read",
        "overmind:observability:write",
        "overmind:data:write",
        "overmind:evaluate",
        "overmind:train",
        "overmind:deploy",
        "overmind:optimize",
        "overmind:connectors",
    }
)


class CatalogError(ValueError):
    """Raised when a catalog definition is not safe to publish."""


class ToolDefinition(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="forbid")

    name: str = Field(pattern=_TOOL_NAME)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(min_length=1, max_length=4_000)
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    read_only: bool
    destructive: bool = False
    idempotent: bool
    open_world: bool
    required_scopes: frozenset[str] = Field(default_factory=frozenset)
    cost_class: CostClass
    async_mode: AsyncMode

    @field_validator("required_scopes")
    @classmethod
    def validate_scopes(cls, values: frozenset[str]) -> frozenset[str]:
        unknown = values - _KNOWN_SCOPES
        if unknown:
            raise ValueError("required_scopes contains an unknown scope")
        return values

    @model_validator(mode="after")
    def validate_metadata(self) -> ToolDefinition:
        if not self.required_scopes:
            raise CatalogError("catalog tools must declare a required scope")
        return self

    def model_post_init(self, __context: Any) -> None:
        if self.name == "ping" or self.name.startswith("notifications_"):
            raise CatalogError("protocol methods cannot be catalog tools")
        if self.name not in _ALLOWED_LIFECYCLE_TOOLS and any(
            part in self.name for part in _FORBIDDEN_NAME_PARTS
        ):
            raise CatalogError("destructive lifecycle tools are not supported")
        if self.destructive:
            raise CatalogError("destructive tools are not supported")

    def as_mcp_tool(self) -> types.Tool:
        return types.Tool(
            name=self.name,
            title=self.title,
            description=self.description,
            inputSchema=_compact_schema(self.input_model.model_json_schema(by_alias=True)),
            annotations=annotations_for(self),
        )


def _compact_schema(value: Any) -> Any:
    if isinstance(value, list):
        return [_compact_schema(item) for item in value]
    if not isinstance(value, dict):
        return value

    compacted = {
        key: _compact_schema(item) for key, item in value.items() if key not in _SCHEMA_NOISE_KEYS
    }
    any_of = compacted.get("anyOf")
    if isinstance(any_of, list) and len(any_of) == 2:
        null_schema = next((item for item in any_of if item == {"type": "null"}), None)
        value_schema = next((item for item in any_of if item != {"type": "null"}), None)
        if (
            null_schema is not None
            and isinstance(value_schema, dict)
            and isinstance(value_schema.get("type"), str)
        ):
            compacted.pop("anyOf")
            compacted.update(value_schema)
            compacted["type"] = [value_schema["type"], "null"]
    return compacted


ToolHandler = Callable[[BaseModel, MCPContext], BaseModel | Awaitable[BaseModel]]


class CatalogEntry:
    __slots__ = ("definition", "handler")

    def __init__(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        self.definition = definition
        self.handler = handler


class ToolCatalog:
    def __init__(self, entries: Iterable[tuple[ToolDefinition, ToolHandler]] = ()) -> None:
        self._entries: dict[str, CatalogEntry] = {}
        for definition, handler in entries:
            self.register(definition, handler)

    def register(self, definition: ToolDefinition, handler: ToolHandler) -> None:
        if definition.name in self._entries:
            raise CatalogError("tool names must be unique")
        if not callable(handler):
            raise CatalogError("tool handler must be callable")
        self._entries[definition.name] = CatalogEntry(definition, handler)

    def get(self, name: str) -> CatalogEntry | None:
        return self._entries.get(name)

    def definitions(self) -> tuple[ToolDefinition, ...]:
        return tuple(entry.definition for entry in self._entries.values())

    def tools(self, permissions: frozenset[str]) -> list[types.Tool]:
        return [
            entry.definition.as_mcp_tool()
            for entry in self._entries.values()
            if self._visible(entry.definition, permissions)
        ]

    async def call(
        self,
        name: str,
        arguments: dict[str, Any],
        context: MCPContext,
    ) -> types.CallToolResult:
        entry = self.get(name)
        if entry is None:
            return error_result(MCPError("invalid_tool", "The requested tool is not available."))
        scope = context.token.scope if isinstance(context.token.scope, dict) else {}
        permissions = frozenset(scope.get("permission", []))
        if not self._visible(entry.definition, permissions):
            return error_result(
                MCPError("permission_denied", "The API key does not grant access to this tool.")
            )
        try:
            payload = entry.definition.input_model.model_validate(arguments)
        except ValidationError as error:
            fields = {
                ".".join(map(str, issue["loc"])) or "arguments": issue["msg"][:300]
                for issue in error.errors(include_input=False, include_url=False)[:20]
            }
            return error_result(
                MCPError(
                    "invalid_input",
                    "Tool arguments do not match its schema: " + ", ".join(fields),
                    fields=fields,
                )
            )

        try:
            result = entry.handler(payload, context)
            if inspect.isawaitable(result):
                result = await result
            output = entry.definition.output_model.model_validate(result)
        except MCPError as error:
            return error_result(error)
        except ValidationError:
            return error_result(MCPError("invalid_output", "The tool returned an invalid result."))
        except Exception:
            logger.exception("MCP tool %s failed for project %s", name, context.project.id)
            return error_result(internal_error())

        structured = output.model_dump(mode="json", by_alias=True)
        return tool_result(structured)

    @staticmethod
    def _visible(definition: ToolDefinition, permissions: frozenset[str]) -> bool:
        permission = "read" if definition.read_only else "write"
        return permission in permissions


CATALOG = ToolCatalog()

from overbae.services.mcp.tools_observability import (  # noqa: E402
    register_observability_tools,
)

register_observability_tools(CATALOG)

from overbae.services.mcp.tools_datasets import register_dataset_tools  # noqa: E402

register_dataset_tools(CATALOG)

from overbae.services.mcp.tools_evaluations import register_evaluation_tools  # noqa: E402

register_evaluation_tools(CATALOG)

from overbae.services.mcp.tools_finetuning import register_finetuning_tools  # noqa: E402

register_finetuning_tools(CATALOG)

from overbae.services.mcp.tools_model_catalog import (  # noqa: E402
    register_model_catalog_tools,
)

register_model_catalog_tools(CATALOG)

from overbae.services.mcp.tools_optimizer import register_optimizer_tools  # noqa: E402

register_optimizer_tools(CATALOG)

from overbae.services.mcp.tools_connectors import register_connector_tools  # noqa: E402

register_connector_tools(CATALOG)

from overbae.services.mcp.tools_instrumentation import (  # noqa: E402
    register_instrumentation_tools,
)

register_instrumentation_tools(CATALOG)
