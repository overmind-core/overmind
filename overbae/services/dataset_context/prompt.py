"""Fuse the deterministic spine with the data capability's in-run semantic layer.

The capability emits the semantic half in its run report under
``report["dataset_card"]``. Never hard-blocks the bundle: a missing, empty or
invalid block degrades to :func:`build_fallback_card`, which is always valid.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _spine_columns(spine: dict[str, Any]) -> set[str]:
    """Columns the spine actually carries (provenance, else the distribution)."""
    allowed = set((spine.get("provenance") or {}).get("columns") or [])
    if not allowed:
        allowed = set(((spine.get("distribution") or {}).get("columns") or {}).keys())
    return allowed


def _known_row_ids(spine: dict[str, Any]) -> set[str]:
    return {
        str(r).strip()
        for r in (spine.get("provenance") or {}).get("row_ids") or []
        if str(r).strip()
    }


def _restrict_row_ids(value: Any, known: set[str]) -> list[str]:
    """Cited row ids, restricted to known spine row ids when those are available."""
    ids = [str(v).strip() for v in (value if isinstance(value, list) else []) if str(v).strip()]
    if known:
        ids = [i for i in ids if i in known]
    return ids


def _semantic_from_report(
    report: dict[str, Any] | None, spine: dict[str, Any]
) -> dict[str, Any] | None:
    """Extract the card's semantic layer from ``report['dataset_card']``.

    Capability-cited columns and row ids are restricted to what the spine actually
    carries, so the block can never invent either. ``None`` when the capability omitted
    or garbled the block, and the caller must fall back deterministically.
    """
    block = (report or {}).get("dataset_card")
    if not isinstance(block, dict) or not block:
        return None

    allowed = _spine_columns(spine)
    known_ids = _known_row_ids(spine)

    raw_mapping = block.get("io_mapping")
    io_mapping: dict[str, str] = {}
    if isinstance(raw_mapping, dict):
        for key, role in raw_mapping.items():
            col = str(key).strip()
            if not col:
                continue
            # Dotted paths address sub-fields of structured columns: keep them
            # when the ROOT column is real.
            root = col.split(".", 1)[0]
            if not allowed or root in allowed:
                io_mapping[col] = str(role).strip().lower()

    reference_columns = [
        c
        for c in (block.get("reference_columns") or [])
        if isinstance(c, str) and (not allowed or c in allowed)
    ]
    reference_description = str(block.get("reference_description") or "").strip()
    reference: dict[str, Any] = {}
    if reference_columns or reference_description:
        reference = {"columns": reference_columns, "description": reference_description}

    quality_signals: list[Any] = []
    for item in block.get("quality_signals") or []:
        if isinstance(item, dict):
            if str(item.get("signal") or "").strip():
                quality_signals.append(item)
        elif str(item).strip():
            quality_signals.append({"signal": str(item).strip(), "severity": "weighted"})

    failure_modes: list[dict[str, Any]] = []
    for item in block.get("failure_modes") or []:
        if isinstance(item, dict):
            description = str(item.get("description") or "").strip()
            if not description:
                continue
            failure_modes.append(
                {
                    "description": description,
                    "detection_pattern": str(item.get("detection_pattern") or "").strip(),
                    "example_row_ids": _restrict_row_ids(item.get("example_row_ids"), known_ids),
                }
            )
        elif str(item).strip():
            failure_modes.append(
                {"description": str(item).strip(), "detection_pattern": "", "example_row_ids": []}
            )

    slice_exemplars: list[dict[str, Any]] = []
    for item in block.get("slice_exemplars") or []:
        if not isinstance(item, dict):
            continue
        entry = {
            "slice": str(item.get("slice") or "").strip(),
            "good_row_ids": _restrict_row_ids(item.get("good_row_ids"), known_ids),
            "bad_row_ids": _restrict_row_ids(item.get("bad_row_ids"), known_ids),
            "criteria": str(item.get("criteria") or "").strip(),
        }
        if entry["slice"] or entry["good_row_ids"] or entry["bad_row_ids"]:
            slice_exemplars.append(entry)

    return {
        "io_mapping": io_mapping,
        "reference": reference,
        "quality_signals": quality_signals,
        "failure_modes": failure_modes,
        "label_space_assessment": block.get("label_space_assessment") or {},
        "slice_exemplars": slice_exemplars,
        "contract_conformance": [
            c for c in (block.get("contract_conformance") or []) if isinstance(c, dict)
        ],
        "summary": str(block.get("summary") or "").strip(),
    }


def build_dataset_card(
    spine: dict[str, Any],
    *,
    report: dict[str, Any] | None = None,
    codebase_card: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fuse the spine + the capability's in-run semantic layer into a validated card.

    Never hard-blocks: an unusable semantic block degrades to
    :func:`build_fallback_card`. ``codebase_card`` gates ``contract_conformance``
    — that join is defined against the codebase card's output contract, so
    without one the capability's guesses are dropped.
    """
    from overbae.services.dataset_context.artifacts import (
        build_fallback_card,
        normalize_dataset_card,
        validate_dataset_card,
    )

    semantic = _semantic_from_report(report, spine)
    if semantic is not None and codebase_card is None:
        semantic["contract_conformance"] = []
    if semantic is not None:
        candidate = normalize_dataset_card({**spine, **semantic})
        if not validate_dataset_card(candidate):
            return candidate
        logger.info("dataset_context: report semantic card invalid; using deterministic fallback")

    return normalize_dataset_card(build_fallback_card(spine))
