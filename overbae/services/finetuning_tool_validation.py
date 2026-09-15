"""Local validation for tool-calling conversational fine-tuning rows.

Together AI rejects datasets whose tool ``tool_call_id`` values do not match the
preceding assistant ``tool_calls``, that carry orphan tool messages, or whose
tool-call counts do not line up. These checks run before upload so the user sees
the problem at dataset selection time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_FN_CHARS = r"A-Za-z0-9 _/'.\-"
_TEXT_TOOL_RE = re.compile(rf"\[([A-Za-z/][{_FN_CHARS}]{{0,80}})\([^)]{{0,500}}\)\]")
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")


def tool_names_from_row(row: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for tool in row.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        name = (tool.get("function") or {}).get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def normalize_tool_label(name: str) -> str:
    return _NON_ALNUM_RE.sub("", (name or "").lower())


def tool_name_tokens(name: str) -> list[str]:
    return [token for token in _NON_ALNUM_RE.split((name or "").lower()) if token]


def resolve_tool_name(name: str, tool_names: set[str]) -> str:
    """Map a tool-call name to the canonical name from the row's ``tools`` list."""
    if not name or not tool_names:
        return name
    if name in tool_names:
        return name

    norm = normalize_tool_label(name)
    if not norm:
        return name

    exact_norm = [tn for tn in tool_names if normalize_tool_label(tn) == norm]
    if len(exact_norm) == 1:
        return exact_norm[0]

    tokens = tool_name_tokens(name)
    if tokens:
        token_matches = [
            tn for tn in tool_names if all(token in tool_name_tokens(tn) for token in tokens)
        ]
        if len(token_matches) == 1:
            return token_matches[0]
        if token_matches:
            return min(token_matches, key=lambda tn: len(tool_name_tokens(tn)))

    substring_matches = [
        tn
        for tn in tool_names
        if norm in normalize_tool_label(tn) or normalize_tool_label(tn) in norm
    ]
    if len(substring_matches) == 1:
        return substring_matches[0]
    if substring_matches:
        return min(substring_matches, key=lambda tn: abs(len(normalize_tool_label(tn)) - len(norm)))

    return name


@dataclass
class ToolCallingCheckResult:
    issue_count: int = 0
    affected_examples: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)


def row_uses_tool_calling(row: dict[str, Any]) -> bool:
    messages = row.get("messages")
    if not isinstance(messages, list):
        return False
    return any(
        isinstance(m, dict) and (m.get("role") == "tool" or bool(m.get("tool_calls")))
        for m in messages
    )


def check_tool_calling_rows(
    rows: list[dict[str, Any]],
    *,
    max_errors: int = 10,
) -> ToolCallingCheckResult:
    issues: list[str] = []
    affected: set[int] = set()
    tool_rows = 0

    for example_idx, row in enumerate(rows):
        messages = row.get("messages")
        if not isinstance(messages, list) or not row_uses_tool_calling(row):
            continue
        tool_rows += 1
        example_issues = _validate_example_messages(example_idx, messages, row)
        if example_issues:
            affected.add(example_idx)
            issues.extend(example_issues)

    errors = issues[:max_errors]
    if len(issues) > max_errors:
        errors.append(f"... and {len(issues) - max_errors} more tool-calling issue(s).")

    return ToolCallingCheckResult(
        issue_count=len(issues),
        affected_examples=len(affected),
        errors=errors,
        stats={
            "tool_calling_examples": tool_rows,
            "tool_calling_issues": len(issues),
            "tool_calling_affected_examples": len(affected),
        },
    )


def _validate_example_messages(
    example_idx: int,
    messages: list[dict[str, Any]],
    row: dict[str, Any] | None = None,
) -> list[str]:
    issues: list[str] = []
    label = f"Example {example_idx + 1}"

    issues.extend(_check_tool_runs(label, messages))
    issues.extend(_check_message_sequence(label, messages))
    if row is not None:
        issues.extend(_check_tool_names(label, messages, row))
    return issues


def _check_tool_names(
    label: str,
    messages: list[dict[str, Any]],
    row: dict[str, Any],
) -> list[str]:
    """Together requires tool_calls / tool message names to match ``tools`` exactly."""
    tool_names = tool_names_from_row(row)
    if not tool_names:
        return []

    issues: list[str] = []
    for msg_idx, msg in enumerate(messages):
        role = msg.get("role")
        if role == "assistant":
            for call_idx, tc in enumerate(msg.get("tool_calls") or []):
                name = (tc.get("function") or {}).get("name")
                if name and name not in tool_names:
                    issues.append(
                        f"{label}, assistant message {msg_idx + 1}, tool_call {call_idx + 1}: "
                        f"function name '{name}' is not in the row's tools list."
                    )
        elif role == "tool":
            name = msg.get("name")
            if name and name not in tool_names:
                issues.append(
                    f"{label}, message {msg_idx + 1}: tool message name '{name}' "
                    "is not in the row's tools list."
                )
    return issues


def _check_tool_runs(label: str, messages: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg.get("role") != "assistant" or not msg.get("tool_calls"):
            i += 1
            continue

        tool_calls = msg.get("tool_calls") or []
        n_calls = len(tool_calls)
        missing_ids = [k + 1 for k, tc in enumerate(tool_calls) if not tc.get("id")]
        if missing_ids:
            issues.append(
                f"{label}, assistant message {i + 1}: "
                f"tool_call(s) {missing_ids} missing required 'id'."
            )

        j = i + 1
        n_tools = 0
        while j < len(messages) and messages[j].get("role") == "tool":
            n_tools += 1
            j += 1

        if n_tools == 0:
            issues.append(
                f"{label}, assistant message {i + 1}: "
                f"issued {n_calls} tool call(s) but no tool response messages follow."
            )
        elif n_tools > n_calls:
            issues.append(
                f"{label}, assistant message {i + 1}: "
                f"issued {n_calls} tool call(s) but {n_tools} tool response(s) follow. "
                "Merge extra responses into one per call or add matching tool_calls."
            )
        elif n_tools < n_calls:
            missing = [tc.get("id", "?") for tc in tool_calls[n_tools:]]
            issues.append(
                f"{label}, assistant message {i + 1}: "
                f"issued {n_calls} tool call(s) but only {n_tools} tool response(s) follow. "
                f"Missing responses for: {', '.join(missing)}."
            )

        content = msg.get("content")
        if content and _TEXT_TOOL_RE.search(content or ""):
            issues.append(
                f"{label}, assistant message {i + 1}: "
                "uses text-format tool calls in content instead of structured tool_calls."
            )

        i = j

    return issues


def _check_message_sequence(label: str, messages: list[dict[str, Any]]) -> list[str]:
    issues: list[str] = []
    pending: list[tuple[str, int]] = []

    for msg_idx, msg in enumerate(messages):
        role = msg.get("role")

        if role == "assistant":
            tool_calls = msg.get("tool_calls") or []
            pending = [(tc.get("id") or "", msg_idx) for tc in tool_calls] if tool_calls else []
            continue

        if role != "tool":
            if role == "user" and pending:
                issues.append(
                    f"{label}, message {msg_idx + 1}: user message appears before all "
                    f"tool responses were sent ({len(pending)} call(s) still pending)."
                )
                pending = []
            continue

        if not pending:
            got_id = msg.get("tool_call_id") or "?"
            issues.append(
                f"{label}, message {msg_idx + 1}: tool response (tool_call_id='{got_id}') "
                "does not follow an assistant message with tool_calls."
            )
            continue

        expected_id, asst_idx = pending[0]
        got_id = msg.get("tool_call_id")
        if got_id != expected_id:
            issued = [tc_id for tc_id, _ in pending]
            issues.append(
                f"{label}, message {msg_idx + 1}: tool_call_id='{got_id}' does not match "
                f"any tool_call id issued by the preceding assistant message "
                f"(preceding assistant issued {issued!r}). Tool responses must directly "
                "follow the assistant turn that issued the corresponding call."
            )
        pending = pending[1:]

    if pending:
        asst_idx = pending[0][1]
        issues.append(
            f"{label}, assistant message {asst_idx + 1}: "
            f"{len(pending)} tool call(s) at end of conversation have no tool responses."
        )

    return issues
