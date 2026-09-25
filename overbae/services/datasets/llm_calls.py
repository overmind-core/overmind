"""One dataset row per ``llm_call`` span.

The row is that call's request and its recorded completion. ``trace_id`` is
omitted on purpose: existing-mode grading treats it as a whole-trace
reconstruction and would score the trace instead of this call.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from overbae.models.traces import Span
from overbae.services.datasets.contract import cut_at_landing, training_line
from overbae.services.datasets.land import STATUS_ERROR, LandError, Landing, bounded_row
from overbae.services.eval import chatml
from overbae.services.finetuning_tool_validation import tool_schema_errors
from overbae.services.finetuning_validator import validate_rows

CANDIDATE_CAP = 10_000
DEFAULT_LIMIT = 200
MAX_LIMIT = 10_000
HASH_POSITION = "hash"

EVAL_MANIFEST = [
    {"name": "source_row", "type": "integer"},
    {"name": "span_id", "type": "string"},
    {"name": "origin_trace_id", "type": "string"},
    {"name": "model", "type": "string"},
    {"name": "capability_id", "type": "string"},
    {"name": "input", "type": "json"},
    {"name": "expected_output", "type": "json"},
]
TRAIN_MANIFEST = [
    {"name": "source_row", "type": "integer"},
    {"name": "span_id", "type": "string"},
    {"name": "origin_trace_id", "type": "string"},
    {"name": "model", "type": "string"},
    {"name": "capability_id", "type": "string"},
    {"name": "messages", "type": "json"},
    {"name": "tools", "type": "json"},
]


class SelectionError(ValueError):
    pass


def _aware(value: datetime) -> datetime:
    if timezone.is_aware(value):
        return value
    return timezone.make_aware(value, timezone.UTC)


def _parse_time(value: Any, *, what: str) -> datetime:
    if isinstance(value, datetime):
        return _aware(value)
    parsed = parse_datetime(str(value or "").strip())
    if parsed is None:
        raise SelectionError(f"{what} must be an ISO timestamp.")
    return _aware(parsed)


@dataclass(frozen=True)
class Selection:
    capability_id: str
    since: datetime
    until: datetime | None = None
    model: str = ""
    limit: int = DEFAULT_LIMIT

    @classmethod
    def parse(cls, payload: Any) -> Selection:
        if not isinstance(payload, dict):
            raise SelectionError("llm_calls must be an object.")
        raw_capability = str(payload.get("capability_id") or "").strip()
        try:
            capability_id = str(uuid.UUID(raw_capability))
        except (ValueError, AttributeError) as exc:
            raise SelectionError("llm_calls.capability_id must be a UUID.") from exc
        since = _parse_time(payload.get("since"), what="since")
        until_raw = payload.get("until")
        until = _parse_time(until_raw, what="until") if until_raw not in (None, "") else None
        if until is not None and until < since:
            raise SelectionError("until is before since.")
        model = str(payload.get("model") or "").strip()
        limit = payload.get("limit", DEFAULT_LIMIT)
        try:
            limit = int(limit)
        except (TypeError, ValueError) as exc:
            raise SelectionError("limit must be a whole number.") from exc
        if not 1 <= limit <= MAX_LIMIT:
            raise SelectionError(f"limit must be between 1 and {MAX_LIMIT}.")
        return cls(
            capability_id=capability_id,
            since=since,
            until=until,
            model=model,
            limit=limit,
        )

    def spec(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "capability_id": self.capability_id,
            "since": self.since.isoformat(),
            "limit": self.limit,
        }
        if self.until is not None:
            out["until"] = self.until.isoformat()
        if self.model:
            out["model"] = self.model
        return out

    def count(self, project_id: Any) -> int:
        return self._queryset(project_id).count()

    def _queryset(self, project_id: Any):
        query = Span.objects.filter(
            project_id=project_id,
            span_type=Span.SpanType.LLM_CALL,
            capability_id=self.capability_id,
            received_at__gte=self.since,
        )
        if self.until is not None:
            query = query.filter(received_at__lte=self.until)
        return query.order_by("-received_at")


def _model_name(span: Span) -> str:
    usage = span.usage or {}
    for key in chatml.MODEL_KEYS:
        if usage.get(key):
            return str(usage[key])
    attrs = span.attributes or {}
    picked = chatml.pick(attrs, chatml.MODEL_KEYS)
    return str(picked) if picked else ""


def _message_lists(attrs: dict[str, Any]) -> tuple[list | None, list | None]:
    request = chatml.parse_messages(chatml.pick(attrs, chatml.INPUT_KEYS))
    completion = chatml.parse_messages(chatml.pick(attrs, chatml.OUTPUT_KEYS))
    if request is None or completion is None:
        indexed_in, indexed_out = chatml.parse_indexed_genai_messages(attrs)
        request = request or indexed_in
        completion = completion or indexed_out
    if request is None or completion is None:
        semconv_in, semconv_out = chatml.parse_genai_semconv_messages(attrs)
        request = request or semconv_in
        completion = completion or semconv_out
    return request, completion


def _last_assistant(messages: list | None) -> dict[str, Any] | None:
    if not messages:
        return None
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "assistant":
            return message
    return None


def _has_body(message: dict[str, Any]) -> bool:
    content = message.get("content")
    if isinstance(content, str) and content.strip():
        return True
    return bool(message.get("tool_calls"))


def _tools(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    tools = chatml.parse_tool_definitions(chatml.pick(attrs, chatml.TOOL_DEF_KEYS))
    if tools:
        return chatml.openai_wire_tools(tools)
    raw = chatml.maybe_parse_json(chatml.pick(attrs, chatml.INPUT_KEYS))
    if isinstance(raw, dict):
        nested = chatml.parse_tool_definitions(raw.get("tools"))
        if nested:
            return chatml.openai_wire_tools(nested)
    return []


def call_record(span: Span) -> dict[str, Any] | None:
    """The request, the recorded assistant turn, and both dataset shapes.

    ``None`` when the span is an error or has no request and completion.
    """
    if span.status_code == STATUS_ERROR:
        return None
    attrs = span.attributes or {}
    request, completion = _message_lists(attrs)
    assistant = _last_assistant(completion)
    if request and request[-1].get("role") == "assistant":
        if assistant is None:
            assistant = request[-1]
        request = request[:-1]
    if not request or assistant is None or not _has_body(assistant):
        return None
    request = chatml.openai_wire_messages(request)
    assistant = chatml.openai_wire_messages([assistant])[0]
    tools = _tools(attrs)
    if tool_schema_errors(tools):
        tools = []
    payload: dict[str, Any] = {"messages": request}
    if tools:
        payload["tools"] = tools
    return {
        "span_id": span.span_id,
        "origin_trace_id": span.trace_id,
        "model": _model_name(span),
        "capability_id": str(span.capability_id) if span.capability_id else "",
        "input": payload,
        "expected_output": assistant,
        "messages": [*request, assistant],
        "tools": tools or None,
    }


def _passes(record: dict[str, Any], intents: Iterable[str]) -> bool:
    wanted = set(intents)
    if "train" in wanted:
        try:
            line = training_line(record)
        except ValueError:
            return False
        if not validate_rows([line]).valid:
            return False
        if cut_at_landing(record.get("messages")):
            return False
    if "eval" in wanted:
        if not record.get("input") or record.get("expected_output") in (None, "", []):
            return False
        if tool_schema_errors((record.get("input") or {}).get("tools")):
            return False
        if cut_at_landing(record.get("input")) or cut_at_landing(record.get("expected_output")):
            return False
    return True


def shape(records: list[dict[str, Any]], intent: str) -> list[dict[str, Any]]:
    if intent == "train":
        keys = ("span_id", "origin_trace_id", "model", "capability_id", "messages", "tools")
    else:
        keys = ("span_id", "origin_trace_id", "model", "capability_id", "input", "expected_output")
    return [{key: record.get(key) for key in keys} for record in records]


def manifest_for(intent: str) -> list[dict[str, str]]:
    return list(TRAIN_MANIFEST if intent == "train" else EVAL_MANIFEST)


def hash_split(
    records: list[dict[str, Any]], eval_percent: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Stable cut on ``span_id``. The same call does not change sides between runs."""
    train: list[dict[str, Any]] = []
    evaluation: list[dict[str, Any]] = []
    for record in records:
        bucket = int(hashlib.sha256(str(record["span_id"]).encode()).hexdigest()[:8], 16) % 100
        (evaluation if bucket < eval_percent else train).append(record)
    if not train or not evaluation:
        raise LandError("The split left one side empty. Widen the window or change the eval share.")
    return train, evaluation


def read(project_id: Any, spec: dict[str, Any], *, intents: Iterable[str]) -> Landing:
    selection = Selection.parse(spec)
    kept: list[dict[str, Any]] = []
    skipped = 0
    query = selection._queryset(project_id).only(
        "span_id",
        "trace_id",
        "status_code",
        "attributes",
        "usage",
        "capability_id",
    )
    for span in query[:CANDIDATE_CAP]:
        if selection.model and _model_name(span) != selection.model:
            skipped += 1
            continue
        record = call_record(span)
        if record is None:
            skipped += 1
            continue
        record = bounded_row(record)
        if not _passes(record, intents):
            skipped += 1
            continue
        kept.append(record)
        if len(kept) >= selection.limit:
            break
    if not kept:
        raise LandError("No LLM calls matched the selection.")
    landing_spec = {**selection.spec(), "kept": len(kept), "skipped": skipped}
    return Landing(kept, kind="llm_calls", spec=landing_spec)
