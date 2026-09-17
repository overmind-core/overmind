"""Landing: rows arrive verbatim as cell 0, stamped with ``source_row``.

A trace source lands one row per trace: the traces-table facts, the wire
transcript, the delivered I/O and the trace's score. Landing never rejects a
row and never triggers scoring. It proposes the capability rank and the intent.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import random
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from django.db import transaction
from django.utils import timezone

from overbae.models import Cell, Dataset, TaskExecution
from overbae.models.traces import Span
from overbae.services.datasets import alignment, contract, files, measure, paths, selection, store

logger = logging.getLogger(__name__)


class LandError(ValueError):
    pass


SPLIT_POSITIONS = ("head", "tail", "random")


@dataclass
class Landing:
    """A source read once: the rows and how they arrived."""

    rows: list[dict[str, Any]]
    kind: str = "file"
    spec: dict[str, Any] = field(default_factory=dict)
    manifest: list[dict] | None = None

    def split(self, *, eval_percent: int, position: str) -> tuple[Landing, Landing]:
        """The train part and the eval part. The eval slice is ``eval_percent`` of
        the rows, at least one and never all, taken from the head, the tail, or a
        fixed-seed random draw; both parts keep the source order."""
        if position not in SPLIT_POSITIONS:
            raise LandError(f"position must be one of {', '.join(SPLIT_POSITIONS)}.")
        n = len(self.rows)
        if n < 2:
            raise LandError("Two rows are needed to split.")
        # Half rounds up in integers, the rule the wizard's preview uses; ``round``
        # would send a tie to the even count and land one row off the preview.
        k = min(max((n * eval_percent + 50) // 100, 1), n - 1)
        if position == "head":
            held = set(range(k))
        elif position == "tail":
            held = set(range(n - k, n))
        else:
            held = set(random.Random(n).sample(range(n), k))
        train = [row for i, row in enumerate(self.rows) if i not in held]
        evaluation = [row for i, row in enumerate(self.rows) if i in held]
        return replace(self, rows=train), replace(self, rows=evaluation)


def _stamp_source_rows(rows: list[dict[str, Any]]) -> None:
    for offset, row in enumerate(rows):
        row[store.SOURCE_ROW] = offset


@transaction.atomic
def commit(
    dataset: Dataset, landing: Landing, *, user: Any = None, state: str = Dataset.State.IDLE
) -> Dataset:
    """Write cell 0, measure it, propose the capability and the intent.
    ``state`` is what the dataset is handed to: the landing task passes
    ``diagnosing`` so the dataset never reads as ready before its first scan."""
    rows = landing.rows
    _stamp_source_rows(rows)
    source = dataset.cells.filter(position=0).first()
    if source is None:
        source = Cell.objects.create(
            dataset=dataset,
            position=0,
            title="Source",
            state=Cell.State.QUEUED,
            created_by=user if getattr(user, "pk", None) else None,
        )
    path = paths.cell_path(dataset.id, source.id)
    store.write_rows(path, rows, landing.manifest)
    fields: dict[str, Any] = {
        "source_kind": landing.kind,
        "source_spec": {**landing.spec, "landed_at": timezone.now().isoformat()},
        "state": state,
        "error": "",
    }
    df = store.read_frame(path)
    fields["capability_rank"] = alignment.rank(dataset.project_id, df)
    if dataset.capability_id is None and fields["capability_rank"]:
        best = fields["capability_rank"][0]
        if best["score"] > 0:
            fields["capability_id"] = best["capability_id"]
    report = contract.measure(df)
    if dataset.intent == Dataset.Intent.PENDING:
        fields["intent"] = contract.propose_intent(df, report)
    Dataset.objects.filter(pk=dataset.pk).update(**fields, updated_at=timezone.now())
    dataset.refresh_from_db()
    measure.frame(dataset, source, path, df=df, report=report, input_fingerprint="", seconds=0.0)
    return dataset


def read_file(path: Path, *, filename: str) -> Landing:
    try:
        rows = files.read_file_rows(path, filename=filename)
    except files.FileError as exc:
        raise LandError(str(exc)) from exc
    if not rows:
        raise LandError("The file has no rows.")
    return read_rows(rows, spec={"filename": filename})


def read_rows(rows: list[dict[str, Any]], *, spec: dict[str, Any] | None = None) -> Landing:
    rows = [dict(r) if isinstance(r, dict) else {"value": r} for r in rows]
    for row in rows:
        row.pop(store.SOURCE_ROW, None)
    return Landing(rows, kind=Dataset.SourceKind.FILE, spec=spec or {})


def land_file(dataset: Dataset, path: Path, *, filename: str, user: Any = None) -> Dataset:
    return commit(dataset, read_file(path, filename=filename), user=user)


def land_rows(
    dataset: Dataset,
    rows: list[dict[str, Any]],
    *,
    spec: dict[str, Any] | None = None,
    user: Any = None,
) -> Dataset:
    return commit(dataset, read_rows(rows, spec=spec), user=user)


TRACE_CHUNK = 200

TRACE_MANIFEST = [
    {"name": "source_row", "type": "integer"},
    {"name": "trace_id", "type": "string"},
    {"name": "conversation_id", "type": "string"},
    {"name": "capability_id", "type": "string"},
    {"name": "capability", "type": "string"},
    {"name": "started_at", "type": "datetime"},
    {"name": "duration_ms", "type": "integer"},
    {"name": "status", "type": "string"},
    {"name": "error", "type": "string"},
    {"name": "model", "type": "string"},
    {"name": "prompt_tokens", "type": "integer"},
    {"name": "completion_tokens", "type": "integer"},
    {"name": "cost_usd", "type": "number"},
    {"name": "input", "type": "json"},
    {"name": "output", "type": "json"},
    {"name": "messages", "type": "json"},
    {"name": "tools", "type": "json"},
    {"name": "score", "type": "number"},
]

STATUS_ERROR = 2


def _ns_to_dt(ns: int | None) -> dt.datetime | None:
    if not ns:
        return None
    return dt.datetime.fromtimestamp(ns / 1e9, tz=dt.UTC)


def _attr(span: Span, key: str) -> Any:
    return (span.attributes or {}).get(key)


def _root(spans: list[Span]) -> Span:
    """The parentless span; an interrupted trace has none, so its longest span stands in."""
    for span in spans:
        if span.parent_span_id is None:
            return span
    return max(spans, key=lambda s: s.duration_ns or 0)


def _delivered(spans: list[Span]) -> Any:
    from overbae.services.eval import chatml

    for span in sorted(spans, key=lambda s: s.start_time_ns or 0, reverse=True):
        if str(_attr(span, "overmind.delivery")).lower() == "true":
            return chatml.maybe_parse_json(chatml.pick(span.attributes or {}, chatml.OUTPUT_KEYS))
    return None


def _first(usage: dict[str, Any], keys: tuple[str, ...]) -> float:
    for key in keys:
        value = usage.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                continue
    return 0.0


def _usage(spans: list[Span]) -> dict[str, Any]:
    """Summed over the trace from ``Span.usage``, which keeps the attribute names
    as sent (``genai.prompt_tokens``, ``gen_ai.usage.input_tokens``, ...)."""
    from overbae.services.eval import chatml

    prompt = completion = 0
    cost = 0.0
    models: list[str] = []
    for span in spans:
        usage = span.usage or {}
        prompt += int(_first(usage, chatml.PROMPT_TOKEN_KEYS))
        completion += int(_first(usage, chatml.COMPLETION_TOKEN_KEYS))
        cost += _first(usage, chatml.COST_KEYS)
        model = next((usage.get(k) for k in chatml.MODEL_KEYS if usage.get(k)), None)
        if model and str(model) not in models:
            models.append(str(model))
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "cost_usd": round(cost, 6),
        "model": models[-1] if models else None,
    }


def _error(root: Span, spans: list[Span]) -> str | None:
    """The root's error first, else the earliest failed span's."""
    for span in (root, *spans):
        if span.status_code != STATUS_ERROR:
            continue
        text = _attr(span, "overmind.error.type") or span.status_message
        if text:
            return str(text)
    return None


def trace_row(spans: list[Span], *, score: float | None) -> dict[str, Any]:
    """One row for one trace: the traces-table facts, the wire transcript and the
    capability's delivered I/O. ``spans`` is the whole trace in start order."""
    from overbae.services.eval import chatml, normalizer

    root = _root(spans)
    trajectory = normalizer.reconstruct_spans(spans, promote_harness=True)
    # The OpenAI wire shape, so a product that keeps `messages` as-is already
    # satisfies the train contract.
    messages = chatml.openai_wire_messages(trajectory.get("messages") or [])
    tools = chatml.openai_wire_tools(trajectory.get("tool_definitions") or [])
    raw_in, raw_out = normalizer.capability_io(spans)
    delivered = _delivered(spans)
    usage = _usage(spans)
    capability = root.capability or next((s.capability for s in spans if s.capability_id), None)
    conversation = next(
        (s.conversation.external_id for s in (root, *spans) if s.conversation_id), None
    )
    failed = any(s.status_code == STATUS_ERROR for s in spans)
    return {
        "trace_id": root.trace_id,
        "conversation_id": conversation or None,
        "capability_id": str(capability.id) if capability is not None else None,
        "capability": capability.name if capability is not None else None,
        "started_at": _ns_to_dt(min(s.start_time_ns or 0 for s in spans) or None),
        "duration_ms": int((root.duration_ns or 0) / 1e6),
        "status": "error" if failed else "ok",
        "error": _error(root, spans),
        "model": usage["model"],
        "prompt_tokens": usage["prompt_tokens"],
        "completion_tokens": usage["completion_tokens"],
        "cost_usd": usage["cost_usd"],
        "input": raw_in,
        "output": delivered if delivered is not None else raw_out,
        "messages": messages or None,
        "tools": tools or None,
        "score": score,
    }


def _chunks(items: Iterable[str], size: int) -> Iterator[list[str]]:
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _scores(project_id: Any, trace_ids: list[str]) -> dict[str, float]:
    """The trace's score is its last scored task execution: the terminal unit of a
    single-task trace, the latest turn of a session."""
    rows = (
        TaskExecution.objects.filter(project_id=project_id, trace_id__in=trace_ids)
        .exclude(success_score=None)
        .order_by("started_at", "created_at")
        .values_list("trace_id", "success_score")
    )
    return dict(rows)


def iter_trace_rows(
    project_id: Any, trace_ids: Iterable[str], *, on_progress: Any = None
) -> Iterator[dict[str, Any]]:
    """Two queries per chunk of traces, then pure assembly; a trace whose spans
    are gone is skipped."""
    done = 0
    for chunk in _chunks(trace_ids, TRACE_CHUNK):
        spans = (
            Span.objects.filter(project_id=project_id, trace_id__in=chunk)
            .select_related("capability", "conversation")
            .order_by("start_time_ns")
        )
        by_trace: dict[str, list[Span]] = {}
        for span in spans:
            by_trace.setdefault(span.trace_id, []).append(span)
        scores = _scores(project_id, chunk)
        for trace_id in chunk:
            trace_spans = by_trace.get(trace_id)
            if not trace_spans:
                continue
            try:
                row = trace_row(trace_spans, score=scores.get(trace_id))
            except Exception:  # noqa: BLE001 — one unreadable trace must not sink the batch
                logger.warning("trace %s did not land", trace_id, exc_info=True)
                continue
            yield bounded_row(row)
        done += len(chunk)
        if on_progress:
            on_progress(done)


def read_traces(project_id: Any, spec: dict[str, Any], *, on_progress: Any = None) -> Landing:
    """One row per trace, in selection order. ``spec`` is a ``TraceSource`` payload."""
    try:
        source = selection.TraceSource.parse(spec)
        rows = list(
            iter_trace_rows(project_id, source.iter_trace_ids(project_id), on_progress=on_progress)
        )
    except selection.TraceSourceError as exc:
        raise LandError(str(exc)) from exc
    if not rows:
        raise LandError("No traces matched. Widen the selection or check the project.")
    return Landing(
        rows, kind=Dataset.SourceKind.TRACES, spec=source.spec(), manifest=list(TRACE_MANIFEST)
    )


def land_traces(
    dataset: Dataset,
    spec: dict[str, Any],
    *,
    user: Any = None,
    on_progress: Any = None,
) -> Dataset:
    return commit(
        dataset, read_traces(dataset.project_id, spec, on_progress=on_progress), user=user
    )


# A tool call whose arguments carry a serialised agent state can weigh hundreds of
# megabytes; a row cell keeps a preview of anything past this and notes the cut.
MAX_CELL_CHARS = 200_000
_PREVIEW_CHARS = 4_000


def _json_prefix(value: Any, limit: int) -> tuple[str, bool]:
    """Up to ``limit`` chars of ``value``'s JSON and whether more followed. The
    streaming encoder stops early, so a huge blob costs ``limit`` work, not its size."""
    chunks: list[str] = []
    total = 0
    for chunk in json.JSONEncoder(default=str).iterencode(value):
        chunks.append(chunk)
        total += len(chunk)
        if total > limit:
            return "".join(chunks)[:limit], True
    return "".join(chunks), False


def _marker(preview: str) -> dict[str, Any]:
    return {contract.CUT_KEY: True, "preview": preview[:_PREVIEW_CHARS]}


def bounded(value: Any, limit: int = MAX_CELL_CHARS, *, depth: int = 0) -> Any:
    """``value`` if its JSON fits ``limit``; otherwise strings are cut and oversized
    members are shrunk in turn, three levels deep, after which a preview marker
    stands in."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + f"…[+{len(value) - limit} chars]"
    text, more = _json_prefix(value, limit)
    if not more:
        return value
    if depth >= 3 or not isinstance(value, (list, dict)):
        return _marker(text)
    if isinstance(value, list):
        per = max(limit // max(len(value), 1), _PREVIEW_CHARS)
        return [bounded(item, per, depth=depth + 1) for item in value]
    per = max(limit // max(len(value), 1), _PREVIEW_CHARS)
    return {key: bounded(item, per, depth=depth + 1) for key, item in value.items()}


def _bounded_tool_call(call: Any, limit: int) -> Any:
    """A wire tool call keeps its shape; only ``function.arguments`` is cut, and the
    cut stays a JSON string so the row still validates."""
    fn = call.get("function") if isinstance(call, dict) else None
    if not isinstance(fn, dict):
        return bounded(call, limit)
    args = fn.get("arguments")
    if isinstance(args, str) and len(args) > limit:
        clipped = json.dumps({contract.CUT_KEY: True, "preview": args[:_PREVIEW_CHARS]})
        return {**call, "function": {**fn, "arguments": clipped}}
    return call


def bounded_messages(messages: Any, limit: int = MAX_CELL_CHARS) -> Any:
    """Per-message bound: keeps every turn's role and shape, cuts only the payloads."""
    if not isinstance(messages, list):
        return bounded(messages, limit)
    out = []
    for message in messages:
        if not isinstance(message, dict):
            out.append(bounded(message, limit))
            continue
        clipped = dict(message)
        calls = clipped.get("tool_calls")
        if isinstance(calls, list) and calls:
            per = max(limit // len(calls), _PREVIEW_CHARS)
            clipped["tool_calls"] = [_bounded_tool_call(c, per) for c in calls]
        for key in ("content", "result", "arguments"):
            if key in clipped:
                clipped[key] = bounded(clipped[key], limit)
        out.append(clipped)
    return out


def bounded_row(row: dict[str, Any]) -> dict[str, Any]:
    for key, value in row.items():
        if key == "messages":
            row[key] = bounded_messages(value)
        elif (
            isinstance(value, (dict, list))
            or isinstance(value, str)
            and len(value) > MAX_CELL_CHARS
        ):
            row[key] = bounded(value)
    return row
