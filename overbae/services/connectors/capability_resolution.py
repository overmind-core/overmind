"""Propose which Overmind Capability an observation name belongs to.

Tiers run strongest first and stop at the first hit: the source paths the scan
recorded, then the names it recorded. Every tier only proposes; nothing binds
until a mapping is saved.
"""

from __future__ import annotations

from typing import Any

from overbae.services.tool_names import canonical_tool_name


def _capability_name_index(capabilities: list[Any]) -> dict[str, Any]:
    """Canonical name -> Capability, from everything the repo scan recorded."""
    index: dict[str, Any] = {}
    for capability in capabilities:
        meta = capability.improvement_metadata or {}
        aliases = [capability.name, capability.slug, capability.entrypoint_fn]
        for mode in meta.get("modes") or []:
            if isinstance(mode, dict):
                aliases += [mode.get("name"), mode.get("entrypoint_fn")]
        for tool in meta.get("tool_spec") or []:
            if isinstance(tool, dict):
                aliases.append(tool.get("name"))
        for alias in aliases:
            if not alias:
                continue
            # First writer wins so a capability's own name beats another's tool.
            index.setdefault(canonical_tool_name(alias), capability)
    return index


def propose_capability_assignments(project, keys: list[str]) -> dict[str, dict[str, Any]]:
    """Map discovered capability keys onto Capabilities, with the evidence for each match."""
    from overbae.models import Capability

    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}

    capabilities = list(Capability.objects.filter(project=project).current())
    if not capabilities:
        return {}

    proposals: dict[str, dict[str, Any]] = {}
    index = _capability_name_index(capabilities)
    for key in keys:
        capability = index.get(canonical_tool_name(key))
        if capability is not None:
            proposals[key] = {
                "capability_id": str(capability.id),
                "capability_name": capability.name,
                "method": "name",
                "evidence": capability.source_path or capability.entrypoint_fn or capability.slug,
            }

    return proposals
