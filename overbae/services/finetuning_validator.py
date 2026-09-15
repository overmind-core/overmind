"""Validates datasets for fine-tuning against the OpenAI-compatible JSONL format.

Two accepted formats: conversational ``{"messages": [...]}`` and instruction
``{"prompt": ..., "completion": ...}``.

Runs entirely in-process — no SDK, no network. The checks replicate what Together's
``check_file`` does locally (UTF-8, JSON per line, minimum samples) and add the
semantic validation Together otherwise defers to its server.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from overbae.services.datasets.text import approx_tokens

logger = logging.getLogger(__name__)

_ALLOWED_ROLES = {"system", "user", "assistant", "tool", "function", "developer"}
_ALLOWED_MSG_KEYS = {
    "role",
    "content",
    "name",
    "function_call",
    "tool_calls",
    "tool_call_id",
    "weight",
    "refusal",
}


@dataclass
class ValidationResult:
    valid: bool
    format: str  # "conversational" | "instruction" | "mixed" | "unknown"
    num_examples: int
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "format": self.format,
            "num_examples": self.num_examples,
            "errors": self.errors,
            "warnings": self.warnings,
            "stats": self.stats,
        }


def validate_rows(rows: list[dict]) -> ValidationResult:
    """Validate already-materialised JSONL rows (no DB or file I/O)."""
    return _apply_tool_calling_checks(_openai_format_check(rows), rows)


def validate_dataset(
    dataset_id: str,
    *,
    validation_enabled: bool = True,
    validation_split_ratio: float = 0.2,
    validation_dataset_id: str | None = None,
    split_method: str = "random",
    cell_id: str | None = None,
    validation_cell_id: str | None = None,
) -> ValidationResult:
    """Validate a dataset's active cell (or the named one); with
    ``validation_enabled`` the ``stats`` also carry the wizard preview's
    train/val counts."""
    from overbae.models import Cell, Dataset
    from overbae.services.datasets import rows as row_store

    dataset = Dataset.objects.filter(pk=dataset_id).first()
    if dataset is None:
        return ValidationResult(
            valid=False,
            format="unknown",
            num_examples=0,
            errors=[f"Dataset {dataset_id} not found."],
        )
    checkpoint = (
        Cell.objects.filter(pk=cell_id, dataset=dataset).first() if cell_id else dataset.active_cell
    )
    if checkpoint is None or not checkpoint.fingerprint:
        return ValidationResult(
            valid=False,
            format="unknown",
            num_examples=0,
            errors=["The dataset has no version that ran."],
        )
    datapoints = list(row_store.iter_rows(checkpoint))
    if not datapoints:
        return ValidationResult(
            valid=False, format="unknown", num_examples=0, errors=["The dataset has no rows."]
        )

    val_datapoints: list | None = None
    if validation_enabled and validation_dataset_id:
        val_dataset = Dataset.objects.filter(pk=validation_dataset_id).first()
        if val_dataset is None:
            return ValidationResult(
                valid=False,
                format="unknown",
                num_examples=len(datapoints),
                errors=[f"Validation dataset {validation_dataset_id} not found."],
            )
        val_checkpoint = (
            Cell.objects.filter(pk=validation_cell_id, dataset=val_dataset).first()
            if validation_cell_id
            else val_dataset.active_cell
        )
        if val_checkpoint is None or not val_checkpoint.fingerprint:
            return ValidationResult(
                valid=False,
                format="unknown",
                num_examples=len(datapoints),
                errors=["The validation dataset has no version that ran."],
            )
        val_datapoints = list(row_store.iter_rows(val_checkpoint))
        if not val_datapoints:
            return ValidationResult(
                valid=False,
                format="unknown",
                num_examples=len(datapoints),
                errors=["The validation dataset has no rows."],
            )

    rows, row_errors = _materialise_rows(datapoints, label_prefix="Row")
    if validation_enabled and val_datapoints is not None:
        val_rows, val_row_errors = _materialise_rows(val_datapoints, label_prefix="Validation row")
        row_errors.extend(val_row_errors)
        rows.extend(val_rows)
    split_stats = _split_preview_stats(
        datapoints,
        val_datapoints,
        validation_enabled=validation_enabled,
        validation_split_ratio=validation_split_ratio,
        validation_dataset_id=validation_dataset_id,
        split_method=split_method,
    )
    split_stats["checkpoint"] = str(checkpoint.id)
    if row_errors:
        return ValidationResult(
            valid=False,
            format="conversational",
            num_examples=len(datapoints),
            errors=row_errors,
            stats=split_stats,
        )
    result = validate_rows(rows[: len(datapoints)])
    if validation_enabled and val_datapoints is not None:
        val_result = validate_rows(rows[len(datapoints) :])
        if not val_result.valid:
            result.valid = False
            result.errors.extend(
                e.replace("Example", "Validation example", 1) for e in val_result.errors
            )
        result.warnings.extend(val_result.warnings)
    result.stats.update(split_stats)
    return result


def _materialise_rows(datapoints: list, *, label_prefix: str) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    for i, dp in enumerate(datapoints, 1):
        try:
            rows.append(row_to_finetuning_line(dp))
        except ValueError as exc:
            if len(errors) < 20:
                errors.append(f"{label_prefix} {i}: {exc}")
    return rows, errors


def _split_preview_stats(
    datapoints: list,
    val_datapoints: list | None,
    *,
    validation_enabled: bool,
    validation_split_ratio: float,
    validation_dataset_id: str | None,
    split_method: str,
) -> dict[str, Any]:
    """Return wizard preview fields merged into ``ValidationResult.stats``."""
    from overbae.services.finetuning_split import split_datapoint_ids

    stats: dict[str, Any] = {
        "total_examples": len(datapoints),
        "train_examples": len(datapoints),
        "val_examples": 0,
        "validation_mode": "off",
        "split_method": split_method,
    }

    if not validation_enabled:
        return stats

    if validation_dataset_id and val_datapoints is not None:
        stats.update(
            {
                "train_examples": len(datapoints),
                "val_examples": len(val_datapoints),
                "validation_mode": "separate",
            }
        )
        return stats

    train_ids, val_ids, _warnings = split_datapoint_ids(
        datapoints,
        validation_split_ratio,
        method=split_method,
    )
    stats.update(
        {
            "train_examples": len(train_ids),
            "val_examples": len(val_ids),
            "validation_mode": "split",
        }
    )
    return stats


def row_to_finetuning_line(row) -> dict:
    """A product row → its OpenAI-compatible JSONL line ``{messages, tools?}``.
    Rows are stored with ``messages`` as a column (``DatasetRow.input`` carries
    it as ``{messages, tools?}``); a malformed one raises ``ValueError``."""
    inp = row.input if hasattr(row, "input") else row
    if isinstance(inp, dict) and isinstance(inp.get("messages"), list):
        messages, tools = inp["messages"], inp.get("tools")
    elif isinstance(inp, list):
        messages, tools = inp, None
    else:
        raise ValueError("not a training row (expected a messages list)")
    if not messages:
        raise ValueError("not a training row (expected a messages list)")
    if not any(isinstance(m, dict) and m.get("role") == "assistant" for m in messages):
        raise ValueError("no assistant turn to train on")
    line: dict = {"messages": messages}
    if tools:
        line["tools"] = tools
    return line


def _long_row_warning_threshold() -> int | None:
    """Smallest max fine-tuning context in the active backend's models.json catalog —
    rows above it cannot train on at least one model. ``None`` disables the warning.
    """
    from django.conf import settings

    from overbae.modal.model_registry import min_sft_context_length

    backend = getattr(settings, "FINETUNING_BACKEND", None)
    # Modal reuses the Baseten catalog verbatim — models.json has no "modal" rows.
    catalog_backend = "baseten" if backend == "modal" else backend
    return min_sft_context_length(catalog_backend) if catalog_backend else None


def _apply_tool_calling_checks(
    result: ValidationResult,
    rows: list[dict],
) -> ValidationResult:
    from overbae.services.finetuning_tool_validation import check_tool_calling_rows

    tool_check = check_tool_calling_rows(rows)
    if tool_check.issue_count == 0:
        result.stats.update(tool_check.stats)
        return result

    result.valid = False
    result.errors.extend(tool_check.errors)
    result.stats.update(tool_check.stats)
    return result


def _openai_format_check(rows: list[dict], *, max_errors: int = 20) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not rows:
        return ValidationResult(
            valid=False,
            format="unknown",
            num_examples=0,
            errors=["No trainable examples found (all datapoints lack output)."],
        )

    detected_format = _detect_format(rows)

    for i, row in enumerate(rows, 1):
        if len(errors) >= max_errors:
            errors.append(f"... and more errors beyond example {i} (first {max_errors} shown).")
            break

        if not isinstance(row, dict):
            errors.append(f"Example {i}: must be a JSON object, got {type(row).__name__}.")
            continue

        label = f"Example {i}"

        if detected_format == "conversational" or "messages" in row:
            errors.extend(_validate_conversational_row(label, row))
        elif detected_format == "instruction" or ("prompt" in row and "completion" in row):
            errors.extend(_validate_instruction_row(label, row))
        else:
            errors.append(
                f"{label}: unrecognised format — row must have 'messages' or 'prompt'+'completion'."
            )

    if len(rows) < 10:
        warnings.append(
            f"Only {len(rows)} example(s). At least 10 are recommended for reliable results."
        )

    if detected_format == "conversational":
        threshold = _long_row_warning_threshold()
        if threshold:
            long = sum(1 for r in rows if approx_tokens(r) > threshold)
            if long:
                warnings.append(
                    f"{long} example(s) may exceed {threshold:,} tokens — "
                    "rows longer than the model's max fine-tuning context will fail "
                    "pre-flight validation before training."
                )

    return ValidationResult(
        valid=len(errors) == 0,
        format=detected_format,
        num_examples=len(rows),
        errors=errors,
        warnings=warnings,
        stats={"total_examples": len(rows), "format": detected_format},
    )


def _validate_conversational_row(label: str, row: dict) -> list[str]:
    issues: list[str] = []

    messages = row.get("messages")
    if not isinstance(messages, list):
        issues.append(f"{label}: 'messages' must be a list.")
        return issues
    if not messages:
        issues.append(f"{label}: 'messages' must not be empty.")
        return issues
    if len(messages) < 2:
        issues.append(
            f"{label}: 'messages' must contain at least 2 messages (got {len(messages)})."
        )
        # Still validate the single message's content.

    has_assistant = False
    for msg_idx, msg in enumerate(messages):
        pos = f"{label}, message {msg_idx + 1}"
        if not isinstance(msg, dict):
            issues.append(f"{pos}: must be a JSON object.")
            continue

        unknown = set(msg.keys()) - _ALLOWED_MSG_KEYS
        if unknown:
            issues.append(
                f"{pos}: unrecognised key(s) {sorted(unknown)} — "
                "only {role, content, name, tool_calls, tool_call_id, weight, refusal} are allowed."
            )

        role = msg.get("role")
        if role not in _ALLOWED_ROLES:
            issues.append(
                f"{pos}: invalid role '{role}' — must be one of {sorted(_ALLOWED_ROLES)}."
            )
            # Don't validate content for an unknown role.
            continue

        if role == "assistant":
            has_assistant = True
            issues.extend(_validate_assistant_message(pos, msg))
        elif role == "tool":
            issues.extend(_validate_tool_message(pos, msg))
        elif role in ("user", "system", "developer"):
            issues.extend(_validate_content_message(pos, msg, role))

    if not has_assistant:
        issues.append(f"{label}: 'messages' must contain at least one 'assistant' message.")

    # Skip when the last role is already invalid — the role check reported it.
    last_msg = messages[-1] if messages else None
    if isinstance(last_msg, dict):
        last_role = last_msg.get("role")
        if last_role in _ALLOWED_ROLES and last_role != "assistant":
            issues.append(
                f"{label}: 'messages' must end with an 'assistant' message "
                f"(last role is '{last_role}')."
            )

    return issues


def _validate_assistant_message(pos: str, msg: dict) -> list[str]:
    issues: list[str] = []
    has_content = msg.get("content") not in (None, "")
    has_tool_calls = bool(msg.get("tool_calls"))

    if not has_content and not has_tool_calls:
        issues.append(f"{pos} (assistant): must have non-empty 'content' or 'tool_calls'.")

    if has_tool_calls:
        tool_calls = msg.get("tool_calls")
        if not isinstance(tool_calls, list):
            issues.append(f"{pos} (assistant): 'tool_calls' must be a list.")
        else:
            for tc_idx, tc in enumerate(tool_calls):
                issues.extend(_validate_tool_call(pos, tc_idx, tc))

    return issues


def _validate_tool_call(pos: str, tc_idx: int, tc: Any) -> list[str]:
    issues: list[str] = []
    tc_pos = f"{pos}, tool_call {tc_idx + 1}"

    if not isinstance(tc, dict):
        issues.append(f"{tc_pos}: must be a JSON object.")
        return issues

    if not tc.get("id"):
        issues.append(f"{tc_pos}: missing required 'id'.")

    if tc.get("type") != "function":
        issues.append(f"{tc_pos}: 'type' must be 'function' (got '{tc.get('type')}').")

    fn = tc.get("function")
    if not isinstance(fn, dict):
        issues.append(f"{tc_pos}: 'function' must be a JSON object.")
        return issues

    if not fn.get("name"):
        issues.append(f"{tc_pos}: 'function.name' is required.")

    args = fn.get("arguments")
    if args is None:
        issues.append(f"{tc_pos}: 'function.arguments' is required.")
    elif not isinstance(args, str):
        issues.append(
            f"{tc_pos}: 'function.arguments' must be a JSON-encoded string, "
            f"got {type(args).__name__}."
        )
    else:
        try:
            json.loads(args)
        except json.JSONDecodeError:
            issues.append(f"{tc_pos}: 'function.arguments' is not valid JSON: {args[:80]!r}.")

    return issues


def _validate_tool_message(pos: str, msg: dict) -> list[str]:
    issues: list[str] = []
    if not msg.get("tool_call_id"):
        issues.append(f"{pos} (tool): missing required 'tool_call_id'.")
    content = msg.get("content")
    if content is None:
        issues.append(f"{pos} (tool): 'content' is required.")
    elif not isinstance(content, str):
        issues.append(
            f"{pos} (tool): 'content' must be a string (got {type(content).__name__}). "
            "Serialise structured results to a JSON string."
        )
    return issues


def _validate_content_message(pos: str, msg: dict, role: str) -> list[str]:
    issues: list[str] = []
    content = msg.get("content")
    if content is None:
        issues.append(f"{pos} ({role}): 'content' is required.")
    elif not isinstance(content, (str, list)):
        issues.append(
            f"{pos} ({role}): 'content' must be a string or content-parts array "
            f"(got {type(content).__name__})."
        )
    return issues


def _validate_instruction_row(label: str, row: dict) -> list[str]:
    issues: list[str] = []
    prompt = row.get("prompt")
    completion = row.get("completion")
    if not isinstance(prompt, str) or not prompt.strip():
        issues.append(f"{label}: 'prompt' must be a non-empty string.")
    if not isinstance(completion, str) or not completion.strip():
        issues.append(f"{label}: 'completion' must be a non-empty string.")
    return issues


def _detect_format(rows: list[dict]) -> str:
    if not rows:
        return "unknown"
    sample = rows[:10]
    has_messages = sum(1 for r in sample if "messages" in r)
    has_prompt = sum(1 for r in sample if "prompt" in r and "completion" in r)

    total = len(sample)
    if has_messages / total > 0.8:
        return "conversational"
    if has_prompt / total > 0.8:
        return "instruction"
    if has_messages > 0 and has_prompt > 0:
        return "mixed"
    return "unknown"
