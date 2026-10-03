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
import re
from dataclasses import dataclass, field
from itertools import islice
from typing import Any

from modal_shared.decisions import decision_line
from overbae.models import Cell, Dataset
from overbae.services.datasets import rows as row_store
from overbae.services.datasets.contract import training_line
from overbae.services.datasets.examples import normalize_record
from overbae.services.datasets.text import approx_tokens
from overbae.services.finetuning_split import split_datapoint_ids

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
    rows = [normalize_record(row) if isinstance(row, dict) else row for row in rows]
    if any(isinstance(row, dict) and "decision" in row for row in rows):
        return validate_decision_rows(rows)
    return _apply_tool_calling_checks(_openai_format_check(rows), rows)


def validate_decision_rows(rows) -> ValidationResult:
    errors = []
    count = 0
    for count, row in enumerate(rows, 1):
        try:
            decision_line(row)
        except (ValueError, AttributeError) as exc:
            if len(errors) < 20:
                errors.append(f"Example {count}: {exc}")
    if not count:
        errors.append("The dataset has no rows.")
    return ValidationResult(valid=not errors, format="decision", num_examples=count, errors=errors)


def _validate_native_dataset(
    checkpoint,
    *,
    validation_enabled,
    validation_split_ratio,
    validation_dataset_id,
    validation_cell_id,
    split_method,
):
    row_store.verify(checkpoint)
    result = validate_decision_rows(row.extra for row in row_store.iter_rows(checkpoint))
    result.stats = {
        "checkpoint": str(checkpoint.id),
        "total_examples": result.num_examples,
        "train_examples": result.num_examples,
        "val_examples": 0,
        "validation_mode": "off",
        "split_method": split_method,
        "format": "decision",
    }
    if not validation_enabled or not result.valid:
        return result
    if validation_dataset_id:
        dataset = Dataset.objects.filter(
            pk=validation_dataset_id, project_id=checkpoint.dataset.project_id
        ).first()
        validation = (
            dataset.cells.filter(pk=validation_cell_id).first()
            if dataset and validation_cell_id
            else dataset.active_cell
            if dataset
            else None
        )
        if validation is None or not validation.fingerprint:
            result.valid = False
            result.errors.append("The validation dataset has no readable version in this project.")
            return result
        row_store.verify(validation)
        checked = validate_decision_rows(row.extra for row in row_store.iter_rows(validation))
        result.valid = checked.valid
        result.errors.extend(
            error.replace("Example", "Validation example", 1) for error in checked.errors
        )
        result.stats.update(val_examples=checked.num_examples, validation_mode="separate")
        overlap = row_store.contamination(checkpoint, validation)["overlap_count"]
        if overlap:
            result.warnings.append(
                f"{overlap} training rows overlap the validation dataset. Validation scores may be inflated."
            )
    else:
        split = checkpoint.dataset.source_spec.get("split", {})
        try:
            training, validation, warnings = split_datapoint_ids(
                row_store.iter_rows(checkpoint),
                validation_split_ratio,
                method=split_method,
                group_by=split.get("group_by", []),
                stratify_by=split.get("stratify_by"),
            )
            result.stats.update(
                train_examples=len(training), val_examples=len(validation), validation_mode="split"
            )
            result.warnings.extend(warnings)
        except ValueError as exc:
            result.valid = False
            result.errors.append(str(exc))
    return result


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
    if (checkpoint.intent_report.get("train") or {}).get("format") == "decision":
        return _validate_native_dataset(
            checkpoint,
            validation_enabled=validation_enabled,
            validation_split_ratio=validation_split_ratio,
            validation_dataset_id=validation_dataset_id,
            validation_cell_id=validation_cell_id,
            split_method=split_method,
        )
    result = stream_chat_validation(checkpoint)
    split_stats = {
        "checkpoint": str(checkpoint.id),
        "total_examples": result.num_examples,
        "train_examples": result.num_examples,
        "val_examples": 0,
        "validation_mode": "off",
        "split_method": split_method,
    }
    if validation_enabled and validation_dataset_id:
        val_dataset = Dataset.objects.filter(
            pk=validation_dataset_id, project_id=dataset.project_id
        ).first()
        val_cell = (
            (
                val_dataset.cells.filter(pk=validation_cell_id).first()
                if validation_cell_id
                else val_dataset.active_cell
            )
            if val_dataset
            else None
        )
        if val_cell is None or not val_cell.fingerprint:
            result.valid = False
            result.errors.append("The validation dataset has no readable version in this project.")
        else:
            val_result = stream_chat_validation(val_cell)
            result.valid = result.valid and val_result.valid
            result.errors.extend(
                error.replace("Example", "Validation example", 1) for error in val_result.errors
            )
            result.warnings.extend(val_result.warnings)
            split_stats.update(val_examples=val_result.num_examples, validation_mode="separate")
            overlap = row_store.contamination(checkpoint, val_cell)["overlap_count"]
            if overlap:
                result.warnings.append(
                    f"{overlap} training rows overlap the validation dataset. Validation scores may be inflated."
                )
    elif validation_enabled:
        split = dataset.source_spec.get("split", {})
        try:
            training, validation, warnings = split_datapoint_ids(
                row_store.iter_rows(checkpoint),
                validation_split_ratio,
                method=split_method,
                group_by=split.get("group_by", []),
                stratify_by=split.get("stratify_by"),
            )
            split_stats.update(
                train_examples=len(training), val_examples=len(validation), validation_mode="split"
            )
            result.warnings.extend(warnings)
        except ValueError as exc:
            result.valid = False
            result.errors.append(str(exc))
    result.stats.update(split_stats)
    return result


def stream_chat_validation(cell):
    row_store.verify(cell)
    source = iter(row_store.iter_rows(cell))
    result = ValidationResult(valid=True, format="unknown", num_examples=0)
    formats = set()
    long_examples = 0
    while batch := list(islice(source, 512)):
        converted, errors = [], []
        for offset, row in enumerate(batch, result.num_examples + 1):
            try:
                converted.append(row_to_finetuning_line(row))
            except ValueError as exc:
                if len(errors) < 20:
                    errors.append(f"Row {offset}: {exc}")
        checked = validate_rows(converted)
        formats.add(checked.format)
        result.valid = result.valid and checked.valid and not errors
        for error in checked.errors:
            errors.append(
                re.sub(
                    r"Example (\d+)", lambda m: f"Example {int(m[1]) + result.num_examples}", error
                )
            )
        result.errors.extend(errors[: max(0, 20 - len(result.errors))])
        long_examples += checked.stats.get("long_examples", 0)
        result.num_examples += len(batch)
        for key in (
            "tool_calling_examples",
            "tool_calling_issues",
            "tool_calling_affected_examples",
        ):
            result.stats[key] = result.stats.get(key, 0) + checked.stats.get(key, 0)
    result.format = next(iter(formats)) if len(formats) == 1 else "mixed" if formats else "unknown"
    if not result.num_examples:
        result.valid = False
        result.errors.append("The dataset has no rows.")
    elif result.num_examples < 10:
        result.warnings.append(
            f"Only {result.num_examples} example(s). At least 10 are recommended for reliable results."
        )
    if long_examples:
        result.warnings.append(
            f"{long_examples} example(s) may exceed {_long_row_warning_threshold():,} tokens; exact preprocessing checks the selected model."
        )
    result.stats.update(format=result.format, total_examples=result.num_examples)
    return result


def row_to_finetuning_line(row) -> dict:
    """A dataset row → its OpenAI-compatible JSONL line ``{messages, tools?}``,
    read from the columns the train contract measured."""
    return training_line(row.extra)


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

    long = 0
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
        stats={"total_examples": len(rows), "format": detected_format, "long_examples": long},
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
