from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
from statistics import mean

from overbae.models import Span

# Below this a judge's score earns a written reason; below FAIL_BELOW the span fails.
REASON_BELOW = 0.75
FAIL_BELOW = 0.5


@dataclass
class Metric:
    expectation: str
    score: float | None = None
    verdict: str = ""
    reason: str = ""
    error: str = ""

    def as_dict(self, row_type: str) -> dict:
        return {"type": row_type, **asdict(self)}


def mean_of(scores) -> float | None:
    """None when nothing was scored: every metric abstained or errored."""
    scored = [score for score in scores if score is not None]
    return mean(scored) if scored else None


def mean_score(metrics: list[Metric]) -> float | None:
    return mean_of(metric.score for metric in metrics)


def score_of(span: Span) -> float | None:
    return span.eval_attributes.get("score")


def scores_of(spans: list[Span]) -> list[float]:
    return [score for span in spans if (score := score_of(span)) is not None]


def is_failing(span: Span) -> bool:
    score = score_of(span)
    return score is not None and score < FAIL_BELOW


def is_final(span: Span) -> bool:
    return "score" in span.eval_attributes


def group_by(spans: list[Span], key) -> dict[str, list[Span]]:
    groups = defaultdict(list)
    for span in spans:
        if value := key(span):
            groups[value].append(span)
    return groups


def roots_of(spans: list[Span]) -> list[Span]:
    return [span for span in spans if span.is_root]


def answers_for_capability(span: Span, trace: dict[str, Span]) -> bool:
    """The trace root, or the topmost span of a capability: the span that carries its answer."""
    if span.is_root:
        return True
    if span.capability_id is None:
        return False
    parent = trace.get(span.parent_span_id)
    return parent is None or parent.capability_id != span.capability_id
