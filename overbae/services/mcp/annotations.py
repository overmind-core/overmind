"""MCP tool annotation mapping."""

from __future__ import annotations

from typing import TYPE_CHECKING

from mcp.types import ToolAnnotations

if TYPE_CHECKING:
    from overbae.services.mcp.catalog import ToolDefinition


def annotations_for(definition: ToolDefinition) -> ToolAnnotations:
    return ToolAnnotations(
        readOnlyHint=definition.read_only,
        destructiveHint=definition.destructive,
        idempotentHint=definition.idempotent,
        openWorldHint=definition.open_world,
    )
