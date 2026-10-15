from __future__ import annotations

from typing import Literal

from overbae.innermind import checks, default_set
from overbae.models import EvalMatrix

DefinitionSource = Literal["stock", "code", "config", "custom", "unknown"]


def definition_source(row: EvalMatrix) -> DefinitionSource:
    """Identify the current definition only when its saved content matches."""
    if row.capability_id:
        meta = row.capability.improvement_metadata or {}
        for entry in meta.get("expectations") or []:
            if (
                row.type == EvalMatrix.Type.LLM
                and row.source == str(entry.get("expectation") or "").strip()[:1_000]
                and row.span_name == str(entry.get("span_name") or "")[:255]
            ):
                return "code"
        for entry in meta.get("eval_matrix") or []:
            if not isinstance(entry, dict):
                continue
            name = str(
                entry.get("name") or entry.get("check") or entry.get("managed_name") or ""
            ).strip()[:255]
            if row.name != name:
                continue
            if entry.get("type") == "deterministic":
                try:
                    source = checks.render(str(entry.get("check") or ""), entry.get("params") or {})
                except ValueError:
                    continue
                if (
                    row.type == EvalMatrix.Type.DETERMINISTIC
                    and row.source == source
                    and row.span_name == str(entry.get("span_name") or "")[:255]
                    and row.span_type == str(entry.get("span_type") or "")
                ):
                    return "config"
            elif row.type == EvalMatrix.Type.LLM and row.source == default_set.expectation_of(
                entry
            ):
                return "config"
    elif row.type == EvalMatrix.Type.DETERMINISTIC:
        for name, span_type, _enabled in checks.CATALOG:
            if (
                row.name == name
                and row.span_type == span_type
                and row.source == checks.source(name)
            ):
                return "stock"
    return "custom" if row.created_by_id else "unknown"
