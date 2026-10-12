import json
from collections import defaultdict
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from statistics import mean, median

from innermind.innermind import Span

REASON_BELOW = 0.75
FAIL_BELOW = 0.5
ARG_PREFIX = "overmind.arg."


@dataclass
class Metric:
    expectation: str
    score: float | None = None
    verdict: str = ""
    reason: str = ""
    error: str = ""


@dataclass
class EvaluatorResult:
    name: str
    kind: str
    score: float | None = None
    metrics: list[Metric] = field(default_factory=list)
    skipped: str = ""


def first_text(attributes: dict, *keys: str) -> str:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return ""


def score_of(span: Span) -> float | None:
    return span.eval_attributes.get("score")


def scores_of(spans: list[Span]) -> list[float]:
    return [score for span in spans if (score := score_of(span)) is not None]


def is_failing(span: Span) -> bool:
    score = score_of(span)
    return score is not None and score < FAIL_BELOW


def group_by(spans: list[Span], field: str) -> dict[str, list[Span]]:
    groups = defaultdict(list)
    for span in spans:
        if key := getattr(span, field):
            groups[key].append(span)
    return groups


def roots_of(spans: list[Span]) -> list[Span]:
    return [span for span in spans if not span.parent_id]


def started_at(span: Span) -> datetime:
    start = datetime.fromisoformat(span.start)
    return start if start.tzinfo else start.replace(tzinfo=UTC)


def tool_name(span: Span) -> str:
    return first_text(span.attributes, "tool.name") or span.name


def number(attributes: dict, *keys: str) -> float | None:
    for key in keys:
        with suppress(KeyError, TypeError, ValueError):
            return float(attributes[key])
    return None


def spikes(spans: list[Span], factor: float, *keys: str) -> list[str]:
    values = [
        (span, value)
        for span in spans
        if span.kind == "llm" and (value := number(span.attributes, *keys)) is not None
    ]
    if len(values) < 5:
        return []
    typical = median(value for _, value in values)
    if typical <= 0:
        return []
    return [f"{span.name} ({value:g})" for span, value in values if value > factor * typical]


def requested_tools(output: str) -> list[str]:
    try:
        message = json.loads(output)
    except json.JSONDecodeError:
        return []
    if isinstance(message, dict) and message.get("choices"):
        message = message["choices"][0].get("message", {})
    messages = message if isinstance(message, list) else [message]
    return [
        (call.get("function") or call).get("name", "")
        for message in messages
        if isinstance(message, dict)
        for call in message.get("tool_calls") or []
    ]


def read_input(attributes: dict) -> str:
    explicit = first_text(attributes, "input", "overmind.input")
    if explicit:
        return explicit
    args = {
        key.removeprefix(ARG_PREFIX): value
        for key, value in attributes.items()
        if key.startswith(ARG_PREFIX)
    }
    if len(args) == 1:
        (value,) = args.values()
        if isinstance(value, str):
            return value.strip()
    return json.dumps(args, ensure_ascii=False) if args else ""


def read_output(attributes: dict) -> str:
    return first_text(attributes, "output", "overmind.output")


def read_expectations(attributes: dict) -> list[str]:
    value = attributes.get("overmind.expectations", attributes.get("expectations"))
    # OTLP array attributes can arrive as a JSON-encoded string.
    if isinstance(value, str) and value.lstrip().startswith("["):
        with suppress(json.JSONDecodeError):
            value = json.loads(value)
    if isinstance(value, str):
        value = [value]
    if isinstance(value, (list, tuple)):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    expect = first_text(attributes, "expect", "overmind.expect")
    return [expect] if expect else []


def mean_score(metrics: list[Metric]) -> float | None:
    scores = [metric.score for metric in metrics if metric.score is not None]
    return mean(scores) if scores else None


async def read_intent(span: Span) -> str | None:
    """None while an ancestor of the span has not arrived yet."""
    intent = first_text(span.attributes, "intent", "overmind.intent")
    if intent:
        return intent
    if not span.parent_id:
        return read_input(span.attributes)
    parent = await Span.objects.filter(span_id=span.parent_id).afirst()
    return await read_intent(parent) if parent else None
