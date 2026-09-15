"""Contracts are the card's trajectory paths as ordered code-symbol anchors,
minted per analyzed sha — never re-derived from instrumented code.
"""

from __future__ import annotations

import logging
from typing import Any

from django.utils.text import slugify

from overbae.services.tool_names import canonical_tool_name

logger = logging.getLogger(__name__)


def _path_tool_names(path: dict[str, Any]) -> list[str]:
    """Declared ``tools`` plus every ``may_use`` capability on the backbone
    steps — a conditional tool is still an expected tool of the task."""
    names = [str(n) for n in path.get("tools") or []]
    for step in path.get("steps") or []:
        if not isinstance(step, dict):
            continue
        for cap in step.get("may_use") or []:
            if isinstance(cap, dict) and str(cap.get("tool") or "").strip():
                names.append(str(cap["tool"]))
    return names


def behaviour_tool_set(path: dict[str, Any], tool_spec: list[Any]) -> list[dict[str, Any]]:
    """Intersected with the card's ``tool_spec`` vocabulary: a name outside it is
    an internal callable, never observable as a tool call."""
    spec_by_canon: dict[str, dict[str, Any]] = {}
    for tool in tool_spec or []:
        if isinstance(tool, dict) and str(tool.get("name") or "").strip():
            spec_by_canon.setdefault(canonical_tool_name(tool["name"]), tool)
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for name in _path_tool_names(path):
        if not str(name or "").strip():
            continue
        canon = canonical_tool_name(name)
        spec = spec_by_canon.get(canon)
        if spec is None or canon in seen:
            continue
        seen.add(canon)
        out.append(
            {
                "name": canon,
                "declared_name": str(spec.get("name")),
                "purpose": str(spec.get("purpose") or ""),
                "side_effect": str(spec.get("side_effect") or ""),
            }
        )
    return out


def behaviour_contracts_from_card(card: dict[str, Any]) -> list[dict[str, Any]]:
    """One contract per trajectory path, or a single ``main`` for straight
    pipelines. Anchor references are already AST-verified upstream."""
    anchors = [a for a in card.get("anchors") or [] if isinstance(a, dict) and a.get("qualname")]
    by_qualname = {a["qualname"]: a for a in anchors}
    entry_qualname = next(
        (a["qualname"] for a in anchors if a.get("kind") == "entry_point"),
        anchors[0]["qualname"] if anchors else "",
    )

    # The analyzer is asked to reuse a mode's slug as the matching path id;
    # enforce it here so a drifting model cannot split the card entry and the
    # scored task into two identities.
    mode_names = {
        str(m.get("name") or "")
        for m in card.get("modes") or []
        if isinstance(m, dict) and m.get("name")
    }
    mode_slugs = {
        slugify(str(m.get("name") or ""))
        for m in card.get("modes") or []
        if isinstance(m, dict) and m.get("name")
    }

    contracts: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for path in card.get("trajectory_map") or []:
        if not isinstance(path, dict) or not path.get("id"):
            continue
        key = str(path["id"]).strip()
        mode_name = str(path.get("mode") or "").strip()
        name_slug = slugify(str(path.get("name") or ""))
        if mode_name in mode_names:
            key = slugify(mode_name) or key
        elif name_slug and name_slug in mode_slugs:
            key = name_slug
        if key in seen_keys:
            continue
        seen_keys.add(key)
        sequence = [q for q in path.get("anchors") or [] if q in by_qualname]
        entry = sequence[0] if sequence else entry_qualname
        contracts.append(
            {
                "key": key,
                "name": str(path.get("name") or key),
                "claim": str(path.get("claim") or "code_path"),
                "routing": str(path.get("routing") or ""),
                "entry_anchor": entry,
                "anchor_sequence": sequence,
                "anchors": [by_qualname[q] for q in sequence],
                "sequence": path.get("sequence") or [],
                "steps": path.get("steps") or [],
                "tools": path.get("tools") or [],
                "tool_set": behaviour_tool_set(path, card.get("tool_spec") or []),
                "terminal": path.get("terminal") or {},
                "divergences": path.get("divergences") or [],
                "provenance": path.get("provenance") or [],
            }
        )

    if not contracts and anchors:
        contracts.append(
            {
                "key": "main",
                "name": "Main path",
                "routing": "",
                "entry_anchor": entry_qualname,
                "anchor_sequence": [a["qualname"] for a in anchors],
                "anchors": anchors,
                "sequence": [],
                "steps": [],
                "tools": [],
                "tool_set": [],
                "terminal": {"kind": "emits_record", "description": ""},
                "divergences": [],
                "provenance": [],
            }
        )

    _flag_indistinguishable_pairs(contracts)
    return contracts


def _flag_indistinguishable_pairs(contracts: list[dict[str, Any]]) -> None:
    """Identical observable anchor sequences cannot be told apart by the binder;
    flag both so instrumentation context can demand a separating anchor."""
    for contract in contracts:
        contract["indistinguishable_with"] = []
    by_signature: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for contract in contracts:
        by_signature.setdefault(tuple(contract["anchor_sequence"]), []).append(contract)
    for group in by_signature.values():
        if len(group) < 2:
            continue
        for contract in group:
            contract["indistinguishable_with"] = [
                # No existing anchor separates identical sequences.
                {"behaviour_key": other["key"], "separating_anchor": ""}
                for other in group
                if other is not contract
            ]


def mint_behaviour_registry(capability: Any, analyzed_sha: str) -> dict[str, Any]:
    """Safe to re-run for the same sha: versions are get_or_create on (behaviour, sha)."""
    from overbae.services.behaviour.anchoring import reanchor

    card = (capability.improvement_metadata or {}).get("capability_card") or {}
    contracts = behaviour_contracts_from_card(card)
    outcome = reanchor(capability, contracts, analyzed_sha)
    logger.info(
        "[behaviour] registry for capability %s @ %s: %d carried, %d minted, %d retired",
        capability.id,
        (analyzed_sha or "")[:8],
        len(outcome["carried"]),
        len(outcome["minted"]),
        len(outcome["retired"]),
    )
    return outcome
