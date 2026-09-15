"""Helpers for flattening a capability card onto Capability columns."""

from __future__ import annotations

from typing import Any


def tools_summary_from_card(card: dict[str, Any], fallback: str) -> str:
    tool_spec = card.get("tool_spec")
    if not isinstance(tool_spec, list) or not tool_spec:
        return fallback
    parts: list[str] = []
    for tool in tool_spec:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        purpose = str(tool.get("purpose") or "").strip()
        if name and purpose:
            parts.append(f"{name}: {purpose}")
        elif name:
            parts.append(name)
    return "; ".join(parts) or fallback


def decision_logic_from_card(card: dict[str, Any], fallback: str) -> str:
    lines: list[str] = []
    success = [str(s) for s in (card.get("success_criteria") or []) if str(s).strip()]
    failures = [str(s) for s in (card.get("failure_modes") or []) if str(s).strip()]
    if success:
        lines.append("Success: " + "; ".join(success))
    if failures:
        lines.append("Failure modes: " + "; ".join(failures))
    return "\n".join(lines) or fallback
