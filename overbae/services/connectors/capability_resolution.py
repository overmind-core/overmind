"""Propose which Overmind Capability an observation name belongs to.

Tiers run strongest first and stop at the first hit: the source paths the scan
recorded, then the names it recorded. Every tier only proposes; nothing binds
until a mapping is saved.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from overbae.services.tool_names import canonical_tool_name


def _capability_name_index(
    capabilities: list[Any], *, include_tools: bool = True
) -> dict[str, Any]:
    """Canonical name -> Capability, from everything the repo scan recorded."""
    index: dict[str, Any] = {}
    for capability in capabilities:
        meta = capability.improvement_metadata or {}
        aliases = [capability.name, capability.slug, capability.entrypoint_fn]
        for mode in meta.get("modes") or []:
            if isinstance(mode, dict):
                aliases += [mode.get("name"), mode.get("entrypoint_fn")]
        if include_tools:
            for tool in meta.get("tool_spec") or []:
                if isinstance(tool, dict):
                    aliases.append(tool.get("name"))
        for alias in aliases:
            if not alias:
                continue
            # First writer wins so a capability's own name beats another's tool.
            index.setdefault(canonical_tool_name(alias), capability)
    return index


def _proposal(capability) -> dict[str, Any]:
    return {
        "capability_id": str(capability.id),
        "capability_name": capability.name,
        "method": "name",
        "evidence": capability.source_path or capability.entrypoint_fn or capability.slug,
    }


def propose_capability_assignments(
    project, keys: list[str], *, include_tools: bool = True
) -> dict[str, dict[str, Any]]:
    """Map discovered capability keys onto Capabilities, with the evidence for each match."""
    from overbae.models import Capability

    keys = [k for k in dict.fromkeys(keys) if k]
    if not keys:
        return {}

    capabilities = list(Capability.objects.filter(project=project).current())
    if not capabilities:
        return {}

    proposals: dict[str, dict[str, Any]] = {}
    index = _capability_name_index(capabilities, include_tools=include_tools)
    for key in keys:
        capability = index.get(canonical_tool_name(key))
        if capability is not None:
            proposals[key] = _proposal(capability)

    return proposals


def propose_boundary_assignments(project, keys: list[str]) -> dict[str, dict[str, Any]]:
    """Parent-observation matches only — tool_spec names are not capability boundaries."""
    return propose_capability_assignments(project, keys, include_tools=False)


def best_shapes_by_name(shapes: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    best: dict[str, dict[str, Any]] = {}
    for shape in shapes:
        name = shape.get("name") or ""
        if not name:
            continue
        prior = best.get(name)
        if prior is None or int(shape.get("score") or 0) > int(prior.get("score") or 0):
            best[name] = shape
    return best


def _child_names(best: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    children: dict[str, list[str]] = defaultdict(list)
    for name, shape in best.items():
        parent = shape.get("parent_name")
        if parent and parent in best and parent != name:
            children[str(parent)].append(name)
    return children


def _collect_descendants(children: dict[str, list[str]], name: str, into: set[str]) -> set[str]:
    for child in children.get(name, []):
        if child in into:
            continue
        into.add(child)
        _collect_descendants(children, child, into)
    return into


def descendant_names(shapes: list[dict[str, Any]], name: str) -> list[str]:
    nested = _collect_descendants(_child_names(best_shapes_by_name(shapes)), name, set())
    return sorted(nested)


def nested_names_under(shapes: list[dict[str, Any]], selected: set[str]) -> set[str]:
    best = best_shapes_by_name(shapes)
    children = _child_names(best)
    nested: set[str] = set()
    for name in selected:
        _collect_descendants(children, name, nested)
    return nested


def drop_nested_mapping_names(
    mapping: dict[str, Any], shapes: list[dict[str, Any]]
) -> tuple[dict[str, Any], list[str]]:
    """Remove observation names that would split out of an ancestor also in ``names``."""
    source = (mapping or {}).get("source")
    if source not in (None, "observation_name"):
        return mapping, []
    names = list((mapping or {}).get("names") or [])
    if not names or not shapes:
        return mapping, []
    nested = nested_names_under(shapes, set(names))
    dropped = [name for name in names if name in nested]
    if not dropped:
        return mapping, []
    updated = dict(mapping)
    updated["source"] = source or "observation_name"
    updated["names"] = [name for name in names if name not in nested]
    updated["assignments"] = {
        key: value for key, value in (mapping.get("assignments") or {}).items() if key not in nested
    }
    return updated, dropped


def mapping_from_suggested(suggested: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "source": "observation_name",
        "names": [item["name"] for item in suggested],
        "assignments": {item["name"]: item["capability_id"] for item in suggested},
    }


def union_custom_mapping(
    mapping: dict[str, Any], suggested: list[dict[str, Any]]
) -> dict[str, Any]:
    """Keep caller names, assignments, and fallback; fill missing names from suggestions."""
    names = list(mapping.get("names") or [])
    assignments = dict(mapping.get("assignments") or {})
    if names or not assignments or not suggested:
        return mapping
    filled = mapping_from_suggested(suggested)
    for name, capability_id in assignments.items():
        filled["assignments"][name] = str(capability_id)
        if name not in filled["names"]:
            filled["names"].append(name)
    fallback = mapping.get("fallback_capability_id")
    if fallback:
        filled["fallback_capability_id"] = str(fallback)
    if mapping.get("source"):
        filled["source"] = mapping["source"]
    if mapping.get("key"):
        filled["key"] = mapping["key"]
    return filled


def suggest_parent_boundaries(project, shapes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One observation-name boundary per matching capability; children stay nested."""
    from overbae.models import Capability

    best = best_shapes_by_name(shapes)
    if not best:
        return []
    capabilities = list(Capability.objects.filter(project=project).current())
    if not capabilities:
        return []
    index = _capability_name_index(capabilities, include_tools=False)
    by_capability: dict[str, list[tuple[dict[str, Any], bool, Any]]] = {}
    for name, shape in best.items():
        capability = index.get(canonical_tool_name(name))
        if capability is None:
            continue
        entrypoint = canonical_tool_name(capability.entrypoint_fn) == canonical_tool_name(name)
        by_capability.setdefault(str(capability.id), []).append((shape, entrypoint, capability))

    picked: list[tuple[dict[str, Any], Any]] = []
    for items in by_capability.values():
        items.sort(key=lambda item: (not item[1], -int(item[0].get("score") or 0), item[0]["name"]))
        picked.append((items[0][0], items[0][2]))

    picked_names = {shape["name"] for shape, _capability in picked}
    suggested: list[dict[str, Any]] = []
    for shape, capability in picked:
        parent = shape.get("parent_name")
        if parent and parent in picked_names:
            continue
        alternatives = sorted(
            {
                match["name"]
                for match, _entrypoint, _cap in by_capability[str(capability.id)]
                if match["name"] != shape["name"]
            }
        )
        suggested.append(
            {
                "name": shape["name"],
                "capability_id": str(capability.id),
                "capability_name": capability.name,
                "nested_names": descendant_names(shapes, shape["name"])[:50],
                "alternatives": alternatives[:50],
            }
        )
    suggested.sort(key=lambda item: item["name"])
    return suggested[:50]


def unmapped_root_names(shapes: list[dict[str, Any]], suggested: list[dict[str, Any]]) -> list[str]:
    suggested_names = {item["name"] for item in suggested}
    names: list[str] = []
    seen: set[str] = set()
    for shape in shapes:
        name = shape.get("name") or ""
        if not name or not shape.get("is_root") or name in suggested_names or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return names[:50]
