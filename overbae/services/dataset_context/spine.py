"""Deterministic spine of the Dataset Capability Card.

No LLM: built purely from artifacts an analysis run already produced, plus a
best-effort PII pass. The semantic layer in ``prompt.py`` is fused on top.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

logger = logging.getLogger(__name__)

# Mirrors the fix-scope selector cap upstream.
_MAX_SLICE_ROW_IDS = 200

# A row id is a content_signature (sha1 hex). Capability-sourced citations are filtered
# through this so prose can never pollute a slice's row_ids.
_SIGNATURE_RE = re.compile(r"^[0-9a-f]{40}$")


def _classify_format(profile: dict[str, Any], report: dict[str, Any]) -> str:
    """Map the profiler's anatomy + the report's label signal to a card ``format``."""
    anatomy = profile.get("conversation_anatomy") or {}
    if anatomy.get("detected") or profile.get("data_format") == "conversation":
        return "sft_messages"

    label_dist = report.get("label_distribution") or {}
    classes = label_dist.get("classes") or 0
    if isinstance(classes, int) and 2 <= classes <= 50 and label_dist.get("column"):
        return "classification"

    data_type = str(profile.get("data_type") or "")
    if data_type.startswith("tabular") or data_type == "timeseries":
        # A single input + single supervised target text pair reads as prompt/completion.
        roles = ((profile.get("format_descriptor") or {}).get("roles")) or {}
        if (
            isinstance(roles, dict)
            and roles.get("input")
            and (roles.get("target") or roles.get("answer"))
        ):
            return "prompt_completion"
        return "tabular"
    return "unknown"


def _build_schema(profile: dict[str, Any], manifest: dict[str, Any] | None) -> dict[str, Any]:
    """Roles + per-field descriptors, preferring the schema manifest, else the column union."""
    manifest = manifest or {}
    descriptor = profile.get("format_descriptor") or {}

    roles = manifest.get("roles") or descriptor.get("roles") or {}
    fields = manifest.get("fields") or {}

    if not fields:
        seen: dict[str, str] = {}
        for f in profile.get("files") or []:
            for col in f.get("column_stats") or []:
                name = str(col.get("column") or "").strip()
                if name and name not in seen:
                    seen[name] = "numeric" if col.get("is_numeric") else "categorical"
        fields = {name: {"type": dtype} for name, dtype in seen.items()}

    schema: dict[str, Any] = {
        "kind": manifest.get("kind") or descriptor.get("kind") or profile.get("data_type"),
        "roles": roles,
        "fields": fields,
    }
    target_locator = descriptor.get("target_locator")
    if target_locator:
        schema["target_locator"] = target_locator
    return schema


def _build_distribution(profile: dict[str, Any]) -> dict[str, Any]:
    """Per-column distribution stats, merged across files (first occurrence wins)."""
    columns: dict[str, Any] = {}
    for f in profile.get("files") or []:
        for stat in f.get("column_stats") or []:
            name = str(stat.get("column") or "").strip()
            if not name or name in columns:
                continue
            entry = {k: v for k, v in stat.items() if k != "column"}
            columns[name] = entry
    return {"columns": columns, "numeric_columns": profile.get("numeric_columns") or []}


def _build_label_space(report: dict[str, Any]) -> dict[str, Any]:
    """Class distribution from the report's label_distribution (empty for non-classification)."""
    label_dist = report.get("label_distribution") or {}
    if not label_dist.get("column"):
        return {}
    return {
        "column": label_dist.get("column"),
        "classes": label_dist.get("classes") or 0,
        "balance": label_dist.get("balance") or "",
        "bars": label_dist.get("bars") or [],
        "entropy": label_dist.get("entropy"),
        "normalized_entropy": label_dist.get("normalized_entropy"),
        "gini_imbalance": label_dist.get("gini_imbalance"),
    }


def _build_target_stats(report: dict[str, Any]) -> dict[str, Any]:
    return {
        "class_stats": report.get("class_stats") or [],
        "token_distribution": report.get("token_distribution") or {},
        "label_conflicts": report.get("label_conflicts") or [],
        "eval_readiness": report.get("eval_readiness") or {},
    }


def _build_volume_and_tokens(profile: dict[str, Any], report: dict[str, Any]) -> dict[str, Any]:
    token_dist = report.get("token_distribution") or {}
    return {
        "total_rows": profile.get("total_rows_exact") or 0,
        "total_files": len(profile.get("files") or []),
        "token_budget_fit": profile.get("token_budget_fit") or {},
        "token_percentiles": {
            k: token_dist.get(k)
            for k in ("p5", "p25", "p50", "p75", "p95", "p99", "max")
            if token_dist.get(k) is not None
        },
    }


def _cluster_slices(report: dict[str, Any]) -> list[dict[str, Any]]:
    slices: list[dict[str, Any]] = []
    for c in report.get("clusters") or []:
        if not isinstance(c, dict):
            continue
        slices.append(
            {
                "id": f"cluster:{c.get('id')}",
                "kind": "cluster",
                "label": c.get("label") or "",
                "size": c.get("size") or 0,
                "pct_of_total": c.get("pct_of_total"),
                "row_ids": [],
                "features": c.get("top_features") or [],
                "examples": c.get("examples") or [],
            }
        )
    return slices


def _smell_slices(smells: dict[str, Any] | None) -> list[dict[str, Any]]:
    slices: list[dict[str, Any]] = []
    if not isinstance(smells, dict):
        return slices
    for i, smell in enumerate(smells.get("data_smells") or []):
        if not isinstance(smell, dict):
            continue
        row_ids = [str(r) for r in (smell.get("row_ids") or [])]
        slices.append(
            {
                "id": f"smell:{smell.get('type') or i}",
                "kind": "smell",
                "label": smell.get("message") or smell.get("type") or "data smell",
                "size": smell.get("count") or len(row_ids),
                "row_ids": row_ids,
                "features": {
                    "column": smell.get("column"),
                    "severity": smell.get("severity"),
                    "type": smell.get("type"),
                },
            }
        )
    return slices


def _slice_row_ids(value: Any, *, signatures_only: bool = False) -> list[str]:
    """De-duped, capped row ids from an evidence list; odd shapes degrade to []."""
    if not isinstance(value, list):
        return []
    seen: dict[str, None] = {}
    for v in value:
        rid = str(v).strip()
        if not rid:
            continue
        if signatures_only and not _SIGNATURE_RE.match(rid):
            continue
        seen.setdefault(rid, None)
    return list(seen)[:_MAX_SLICE_ROW_IDS]


def _issue_slices(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Cohorts from the kernel's eval_readiness issues."""
    slices: list[dict[str, Any]] = []
    issues = (report.get("eval_readiness") or {}).get("issues")
    for i, issue in enumerate(issues if isinstance(issues, list) else []):
        if not isinstance(issue, dict):
            continue
        row_ids = _slice_row_ids(issue.get("row_signatures"))
        if not row_ids:
            continue
        slices.append(
            {
                "id": f"issue:{issue.get('id') or issue.get('type') or i}",
                "kind": "issue",
                "label": issue.get("detail") or issue.get("type") or "readiness issue",
                "size": len(row_ids),
                "row_ids": row_ids,
                "features": {"type": issue.get("type"), "severity": issue.get("severity")},
            }
        )
    return slices


def _fix_scope_slices(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Cohorts from capability-proposed fixes' scope selectors."""
    slices: list[dict[str, Any]] = []
    for i, fix in enumerate(report.get("proposed_fixes") or []):
        if not isinstance(fix, dict):
            continue
        scope = fix.get("scope") if isinstance(fix.get("scope"), dict) else {}
        selector = scope.get("selector") if isinstance(scope.get("selector"), dict) else {}
        row_ids = _slice_row_ids(selector.get("signatures"), signatures_only=True)
        if not row_ids:
            continue
        slices.append(
            {
                "id": f"fix:{fix.get('id') or i}",
                "kind": "fix_scope",
                "label": fix.get("title") or "proposed fix scope",
                "size": len(row_ids),
                "row_ids": row_ids,
                "features": {
                    "fields": scope.get("fields") or [],
                    "category": fix.get("category"),
                },
            }
        )
    return slices


def _intent_slices(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Cohorts from agenda coverage example rows."""
    slices: list[dict[str, Any]] = []
    intents = (report.get("agenda_coverage") or {}).get("intents")
    for i, intent in enumerate(intents if isinstance(intents, list) else []):
        if not isinstance(intent, dict):
            continue
        row_ids = _slice_row_ids(intent.get("example_rows"), signatures_only=True)
        if not row_ids:
            continue
        slices.append(
            {
                "id": f"intent:{i}",
                "kind": "intent",
                "label": intent.get("intent") or f"agenda intent {i}",
                "size": len(row_ids),
                "row_ids": row_ids,
                "features": {"coverage_score": intent.get("coverage_score")},
            }
        )
    return slices


def _build_slices(report: dict[str, Any], smells: dict[str, Any] | None) -> list[dict[str, Any]]:
    return (
        _cluster_slices(report)
        + _smell_slices(smells)
        + _issue_slices(report)
        + _fix_scope_slices(report)
        + _intent_slices(report)
    )


def _exact_duplicate_rows(rows: list[dict[str, Any]]) -> int:
    """Count rows beyond the first occurrence of each content signature."""
    from overbae.services.datasets.text import content_signature

    counts: Counter = Counter()
    for r in rows:
        if isinstance(r, dict):
            counts[content_signature(r)] += 1
    return sum(n - 1 for n in counts.values() if n > 1)


def _pii_columns(
    rows: list[dict[str, Any]], manifest: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """The PII scanner left with the old workshop; the agent's diagnosis names PII now."""
    return []


def _build_hygiene(
    rows: list[dict[str, Any]],
    manifest: dict[str, Any] | None,
    report: dict[str, Any],
) -> dict[str, Any]:
    redundancy = report.get("redundancy") or []
    near_dup_pairs = sum(1 for r in redundancy if isinstance(r, dict) and r.get("row_ids"))
    return {
        "pii_columns": _pii_columns(rows, manifest),
        "exact_duplicate_rows": _exact_duplicate_rows(rows),
        "near_duplicate_pairs": near_dup_pairs,
        "outliers": report.get("outliers") or [],
    }


def _columns_from_profile(profile: dict[str, Any]) -> list[str]:
    seen: dict[str, None] = {}
    for f in profile.get("files") or []:
        for col in f.get("column_stats") or []:
            name = str(col.get("column") or "").strip()
            if name:
                seen.setdefault(name, None)
    return list(seen)


def build_spine(
    *,
    profile: dict[str, Any],
    report: dict[str, Any],
    rows: list[dict[str, Any]],
    manifest: dict[str, Any] | None = None,
    smells: dict[str, Any] | None = None,
    dataset_id: str = "",
    dataset_version: Any = None,
    data_version: str = "",
    row_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Assemble the deterministic spine of the Dataset Capability Card.

    ``rows`` are the re-loaded source rows, used for hygiene only.
    ``data_version`` + ``row_ids`` populate provenance so the spine alone is a
    valid card after :func:`build_fallback_card`.
    """
    columns = _columns_from_profile(profile)
    schema = _build_schema(profile, manifest)
    return {
        "format": _classify_format(profile, report),
        "schema": schema,
        "distribution": _build_distribution(profile),
        "label_space": _build_label_space(report),
        "target_stats": _build_target_stats(report),
        "volume_and_tokens": _build_volume_and_tokens(profile, report),
        "slices": _build_slices(report, smells),
        "hygiene": _build_hygiene(rows, manifest, report),
        "provenance": {
            "dataset_id": str(dataset_id or ""),
            "dataset_version": dataset_version,
            "data_version": str(data_version or ""),
            "columns": columns,
            "row_ids": list(row_ids or []),
        },
    }
