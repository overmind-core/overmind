"""Dataset shape inference. The capabilities layer turns the profile into
"which evaluator kinds and scopes apply"."""

from __future__ import annotations

from typing import Any

from overbae.services.eval import chatml, normalizer

_SAMPLE_SIZE = 40
_MAX_TRACE_FETCH = 12
_LONG_TOKENS = 8_000


def profile_dataset(dataset, *, sample_size: int = _SAMPLE_SIZE) -> dict[str, Any]:
    from overbae.models import Span
    from overbae.services.datasets.rows import count as row_count
    from overbae.services.datasets.rows import sample_rows

    points = sample_rows(dataset, sample_size)
    sampled = len(points)
    if sampled == 0:
        return _empty_profile(dataset)

    references = [p.expected_output for p in points if p.expected_output not in (None, "")]
    reference_ratio = len(references) / sampled
    has_reference = reference_ratio >= 0.5
    output_kind = _classify_output_kind(references)

    # A canonical FT row keeps its target as the last assistant turn of
    # ``input["messages"]`` and leaves expected_output empty; pre-intent rows kept it
    # in a bare message list. An eval row may never end on an assistant turn.
    product = dataset.active_cell
    if not has_reference and (product is None or not product.fits("eval")[0]):
        chat_refs = [c for c in (_final_assistant_content(p.input) for p in points) if c]
        if chat_refs and len(chat_refs) / sampled >= 0.5:
            has_reference = True
            reference_ratio = len(chat_refs) / sampled
            output_kind = _classify_output_kind(chat_refs)

    modality_rank = 1
    rank = {"single_turn": 1, "multi_turn": 2, "tool_calling": 3}
    for p in points:
        norm = normalizer.normalize_messages(p.input)
        modality_rank = max(modality_rank, rank.get(norm.get("modality", "single_turn"), 1))

    trace_ids = [p.source_trace_id for p in points if p.source_trace_id][:_MAX_TRACE_FETCH]
    has_tool_calls = False
    step_counts: list[int] = []
    max_tokens = 0
    if trace_ids:
        project_id = getattr(dataset, "project_id", None) or getattr(
            getattr(dataset, "capability", None), "project_id", None
        )
        span_qs = Span.objects.filter(trace_id__in=trace_ids)
        if project_id:
            span_qs = span_qs.filter(project_id=project_id)
        by_trace: dict[str, list] = {}
        for span in span_qs:
            by_trace.setdefault(span.trace_id, []).append(span)
        for spans in by_trace.values():
            # Must go through reconstruct_spans: without folding standalone tool
            # spans in, multi-lane/MCP capabilities give a false ``has_tool_calls`` and
            # capability gating then hides every tool-aware evaluator.
            norm = normalizer.reconstruct_spans(spans)
            struct = normalizer.structure_trajectory(norm)
            n_calls = struct.get("num_tool_calls", 0)
            if n_calls > 0:
                has_tool_calls = True
            step_counts.append(n_calls)
            max_tokens = max(max_tokens, struct.get("approx_tokens", 0))
            if modality_rank < 3 and norm.get("modality") == "tool_calling":
                modality_rank = 3

    modality = {1: "single_turn", 2: "multi_turn", 3: "tool_calling"}[modality_rank]
    if modality == "tool_calling":
        has_tool_calls = True
    avg_steps = (sum(step_counts) / len(step_counts)) if step_counts else 0.0
    max_steps = max(step_counts) if step_counts else 0
    is_long = max_tokens > _LONG_TOKENS or max_steps > 20

    return {
        "count": row_count(product) if product is not None else 0,
        "sampled": sampled,
        "has_reference": has_reference,
        "reference_ratio": round(reference_ratio, 3),
        "output_kind": output_kind,
        "modality": modality,
        "has_tool_calls": has_tool_calls,
        "avg_steps": round(avg_steps, 2),
        "max_steps": max_steps,
        "is_long": is_long,
    }


def _final_assistant_content(value: Any) -> Any:
    messages = value.get("messages") if isinstance(value, dict) else value
    if not isinstance(messages, list):
        return None
    return next(
        (
            m.get("content")
            for m in reversed(messages)
            if isinstance(m, dict) and m.get("role") == "assistant" and m.get("content")
        ),
        None,
    )


def _empty_profile(dataset) -> dict[str, Any]:
    product = dataset.active_cell
    return {
        "count": int(product.rows) if product is not None else 0,
        "sampled": 0,
        "has_reference": False,
        "reference_ratio": 0.0,
        "output_kind": "unknown",
        "modality": "single_turn",
        "has_tool_calls": False,
        "avg_steps": 0.0,
        "max_steps": 0,
        "is_long": False,
    }


def _classify_output_kind(references: list[Any]) -> str:
    if not references:
        return "unknown"
    json_like = 0
    strings: list[str] = []
    for v in references:
        parsed = chatml.maybe_parse_json(v) if isinstance(v, str) else v
        if isinstance(parsed, (dict, list)):
            json_like += 1
        else:
            strings.append(str(parsed))
    if json_like >= 0.6 * len(references):
        return "json"
    if not strings:
        return "free_text"
    avg_len = sum(len(s) for s in strings) / len(strings)
    distinct = len({s.strip().lower() for s in strings})
    cardinality_ratio = distinct / len(strings)
    # Length is the primary signal: a 100-class dataset is still labels even
    # though 50 samples yield ~66% distinct values. Cardinality only rules out
    # short free-text such as one-word summaries.
    if avg_len <= 20:
        return "label"
    if avg_len <= 40 and cardinality_ratio <= 0.75:
        return "label"
    return "free_text"


_CLOSED_FORM_ANSWER_CHARS = 200
_CANONICAL_GOLD_KEYS = (
    "canonical_answer",
    "gold",
    "answer",
    "ground_truth",
    "groundTruth",
)


def is_closed_form_answer(value: Any) -> bool:
    """A canonical short answer, not a same-shape teacher report or JSON record."""
    if value in (None, "", [], {}):
        return False
    if isinstance(value, dict):
        if len(value) == 1:
            key = next(iter(value))
            if str(key).lower() in {"answer", "gold", "text", "value", "canonical_answer"}:
                return is_closed_form_answer(value[key])
        return False
    if isinstance(value, list):
        return False
    text = str(value).strip()
    if not text or len(text) > _CLOSED_FORM_ANSWER_CHARS:
        return False
    return not (text.startswith("#") and "\n" in text)


def closed_form_gold(data: Any) -> Any:
    if not isinstance(data, dict):
        return None
    for key in _CANONICAL_GOLD_KEYS:
        value = data.get(key)
        if is_closed_form_answer(value):
            return value if isinstance(value, str) else str(value).strip()
    return None


def without_leaked_gold(value: Any) -> Any:
    """Canonical gold keys belong on ``expected_output``, not inside ``{input}``."""
    if not isinstance(value, dict):
        return value
    leaked = {k for k in _CANONICAL_GOLD_KEYS if k in value}
    if not leaked:
        return value
    return {k: v for k, v in value.items() if k not in leaked}


def bind_eval_reference(inp: Any, expected: Any) -> tuple[Any, Any]:
    """Promote a closed-form gold off a dict input, then strip those keys."""
    cleaned = without_leaked_gold(inp)
    if expected not in (None, "") or not isinstance(inp, dict):
        return cleaned, expected
    gold = closed_form_gold(inp)
    return cleaned, gold if gold is not None else expected


def dataset_has_closed_form_reference(dataset, *, sample_size: int = _SAMPLE_SIZE) -> bool:
    """True when most eval golds are short canonical answers, not teacher reports."""
    from overbae.services.datasets.rows import sample_rows

    if dataset is None:
        return False
    points = sample_rows(dataset, sample_size)
    if not points:
        return False
    hits = 0
    seen = 0
    for point in points:
        gold = point.expected_output
        if not is_closed_form_answer(gold):
            gold = closed_form_gold(point.extra or {})
        if gold in (None, "") and point.expected_output in (None, ""):
            continue
        seen += 1
        if is_closed_form_answer(gold):
            hits += 1
    return seen > 0 and hits / seen >= 0.6


def closed_form_reference_for(*, capability=None, dataset=None) -> bool:
    # Capability is accepted so card-sync can pass it; a sibling eval set
    # must not rewrite this dataset's evaluator.
    _ = capability
    return dataset_has_closed_form_reference(dataset)
