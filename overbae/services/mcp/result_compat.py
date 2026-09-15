"""Compatibility content for MCP tool results."""

from __future__ import annotations

import json
from typing import Any

from mcp import types
from pydantic import ValidationError

from overbae.services.mcp.contracts.common import ResourceLinkContract


def tool_result(structured: dict[str, Any], *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(structured, separators=(",", ":")),
            ),
            *_resource_links(structured),
        ],
        structuredContent=structured,
        isError=is_error,
    )


def _resource_links(structured: dict[str, Any]) -> list[types.ResourceLink]:
    raw_links: list[Any] = []
    resource = structured.get("resource")
    if isinstance(resource, dict):
        raw_links.append(resource)
    resource_links = structured.get("resource_links")
    if isinstance(resource_links, list):
        raw_links.extend(resource_links)

    links: list[types.ResourceLink] = []
    seen_uris: set[str] = set()
    for raw_link in raw_links:
        try:
            link = ResourceLinkContract.model_validate(raw_link)
            uri = link.uri
            if uri in seen_uris:
                continue
            resource = types.ResourceLink(
                type="resource_link",
                name=uri,
                title=link.title,
                uri=uri,
                mimeType=link.mime_type,
            )
        except (TypeError, ValidationError):
            continue
        seen_uris.add(uri)
        links.append(resource)
    return links
