from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from dataclasses import asdict, replace
from datetime import timedelta

from django.utils import timezone

from overbae.innermind import evidence
from overbae.innermind.evals import JEV_STATE_LIMIT, JevEvaluator
from overbae.innermind.metrics import (
    FAIL_BELOW,
    REASON_BELOW,
    Metric,
    answers_for_capability,
    is_failing,
    is_final,
    mean_of,
    roots_of,
    score_of,
)
from overbae.innermind.span_processor import MAX_ATTEMPTS
from overbae.models import EvalScore, Span

logger = logging.getLogger(__name__)

TRACE_SPAN_CAP = 2_000
# The sweep looks back this far for final roots that still lack a trajectory.
SWEEP_WINDOW = timedelta(days=1)
SWEEP_LIMIT = 500


def is_in_order(expected: list[str], actual: list[str]) -> bool:
    calls = iter(actual)
    return all(name in calls for name in expected)


class TrajectoryEvaluator:
    name: str
    expectation: str
    calls_judge = False

    async def score(self, spans: list[Span]) -> Metric | None: ...


class OutcomeEvaluator(TrajectoryEvaluator):
    """The mean score of the spans that carry an answer: the root and each capability's top."""

    name = "outcome"
    expectation = "The agent achieves the intent."

    async def score(self, spans: list[Span]) -> Metric | None:
        trace = {span.span_id: span for span in spans}
        score = mean_of(score_of(span) for span in spans if answers_for_capability(span, trace))
        if score is None:
            return Metric(self.expectation, error="no answering span has a score")
        return Metric(self.expectation, score=score)


class WeakestStepEvaluator(TrajectoryEvaluator):
    name = "weakest_step"
    expectation = "Every step meets its expectations."

    async def score(self, spans: list[Span]) -> Metric | None:
        steps = [span for span in spans if not span.is_root and score_of(span) is not None]
        if not steps:
            return None
        weakest = min(steps, key=score_of)
        return Metric(
            self.expectation, score=score_of(weakest), reason=f"weakest step: {weakest.name}"
        )


class EfficiencyEvaluator(TrajectoryEvaluator):
    name = "efficiency"
    expectation = "The agent makes no redundant calls and recovers from every error."

    async def score(self, spans: list[Span]) -> Metric | None:
        calls = Counter(
            (evidence.tool_name(span), evidence.read_input(span))
            for span in spans
            if evidence.is_tool(span) and evidence.status(span) == "ok"
        )
        redundant = sum(n - 1 for n in calls.values())
        unrecovered = sum(
            evidence.status(span) == "error"
            and not any(
                later.parent_span_id == span.parent_span_id
                and later.name == span.name
                and evidence.status(later) == "ok"
                for later in spans[i + 1 :]
            )
            for i, span in enumerate(spans)
        )
        llm_calls = [span for span in spans if evidence.is_llm(span)]
        tokens = sum(evidence.tokens(span) or 0 for span in llm_calls)
        cost = sum(evidence.cost(span) or 0 for span in llm_calls)
        seconds = sum(evidence.seconds(span) for span in llm_calls)
        return Metric(
            self.expectation,
            score=max(0.0, 1 - (redundant + unrecovered) / len(spans)),
            reason=(
                f"{redundant} redundant calls, {unrecovered} unrecovered errors, "
                f"{tokens:g} tokens, ${cost:g}, {seconds:g}s in LLM calls"
            ),
        )


class PathJudgeEvaluator(TrajectoryEvaluator):
    name = "path_judge"
    calls_judge = True
    expectation = (
        "Each step moves toward the intent, no step is unnecessary or out of order, "
        "and the agent recovers sensibly from errors."
    )
    step_chars = 300

    async def score(self, spans: list[Span]) -> Metric | None:
        root = roots_of(spans)[0]
        intent = root.eval_attributes.get("intent")
        if not intent:
            return Metric(self.expectation, error="root span has no intent")
        steps = [
            {
                "name": span.name,
                "type": span.span_type,
                "status": evidence.status(span),
                "score": score_of(span),
                "input": evidence.read_input(span)[: self.step_chars],
                "output": evidence.read_output(span)[: self.step_chars],
            }
            for span in spans
            if span is not root
        ]
        while True:
            state = {
                "intent": intent,
                "expectation": self.expectation,
                "input": json.dumps(steps, ensure_ascii=False),
                "output": evidence.read_output(root),
            }
            # Keep the first and last steps; the middle of a long run is the least telling.
            if len(json.dumps(state).encode()) <= JEV_STATE_LIMIT or len(steps) <= 2:
                break
            del steps[len(steps) // 2]
        judge = JevEvaluator(root.project_id)
        metric = await judge.judge(state, self.expectation)
        if metric.score is not None and metric.score < REASON_BELOW:
            metric.reason = await judge.explain(state, metric)
        return metric


class ReferenceEvaluator(TrajectoryEvaluator):
    name = "reference"
    expectation = "The agent calls the reference tools in order."

    async def score(self, spans: list[Span]) -> Metric | None:
        expected = evidence.read_reference(roots_of(spans)[0].attributes or {})
        if not expected:
            return None
        actual = [evidence.tool_name(span) for span in spans if evidence.is_tool(span)]
        recall = sum(name in actual for name in expected) / len(expected)
        precision = sum(name in expected for name in actual) / len(actual) if actual else 0.0
        return Metric(
            self.expectation,
            score=(recall + precision) / 2,
            verdict="in order" if is_in_order(expected, actual) else "out of order",
            reason=f"{recall:.0%} of reference tools called, {precision:.0%} of calls expected",
        )


class TrajectoryProcessor:
    def get_evaluators(self) -> list[TrajectoryEvaluator]:
        raise NotImplementedError

    async def evaluate(self, spans: list[Span], *, judge: bool = True, last_attempt: bool) -> Span:
        """Stores the trajectory on the root span without saving it. A failing evaluator
        raises unless this is the ``last_attempt``; then it, like every judged evaluator
        when ``judge`` is off, is recorded as an error metric."""
        root = roots_of(spans)[0]
        evaluators = self.get_evaluators()
        stopped = Metric("", error=f"scoring failed after {MAX_ATTEMPTS} attempts")

        async def score(evaluator: TrajectoryEvaluator) -> Metric | None:
            if evaluator.calls_judge and not judge:
                return replace(stopped, expectation=evaluator.expectation)
            try:
                return await evaluator.score(spans)
            except Exception:
                if not last_attempt:
                    raise
                return replace(stopped, expectation=evaluator.expectation)

        metrics = await asyncio.gather(*map(score, evaluators))
        by_name = {
            evaluator.name: metric for evaluator, metric in zip(evaluators, metrics, strict=True)
        }
        outcome = by_name[OutcomeEvaluator.name].score
        root.eval_attributes = {
            **root.eval_attributes,
            "trajectory": {
                "span_count": len(spans),
                "score": outcome,
                "passed": None
                if outcome is None
                else outcome >= FAIL_BELOW and not any(map(is_failing, spans)),
                "metrics": {
                    evaluator.name: asdict(metric)
                    for evaluator, metric in zip(evaluators, metrics, strict=True)
                    if metric is not None
                },
            },
        }
        return root


class SingleStepTrajectoryProcessor(TrajectoryProcessor):
    def get_evaluators(self) -> list[TrajectoryEvaluator]:
        return [OutcomeEvaluator()]


class AgentTrajectoryProcessor(TrajectoryProcessor):
    def get_evaluators(self) -> list[TrajectoryEvaluator]:
        return [
            OutcomeEvaluator(),
            WeakestStepEvaluator(),
            EfficiencyEvaluator(),
            PathJudgeEvaluator(),
            ReferenceEvaluator(),
        ]


class TrajectoryProcessorFactory:
    def get_processor(self, spans: list[Span]) -> TrajectoryProcessor:
        if len(spans) == 1:
            return SingleStepTrajectoryProcessor()
        return AgentTrajectoryProcessor()


trajectory_factory = TrajectoryProcessorFactory()


async def store(root: Span, attributes: dict, *, current: dict | None = None) -> bool:
    """Writes the root's eval attributes only if they still hold ``current``, so a
    concurrent pass or a re-ingest's reset is never overwritten."""
    expected = root.eval_attributes if current is None else current
    if not await Span.objects.filter(span_id=root.span_id, eval_attributes=expected).aupdate(
        eval_attributes=attributes
    ):
        return False
    root.eval_attributes = attributes
    return True


def is_ready(spans: list[Span]) -> bool:
    """A trajectory is scored once, when every arrived span is final; spans arriving
    after it do not score it again."""
    roots = roots_of(spans)
    return (
        len(roots) == 1
        and "trajectory" not in roots[0].eval_attributes
        and all(map(is_final, spans))
    )


async def score_trace(trace_id: str) -> bool:
    """Counts the attempt on the root before judging, so a killed pass counts too; once
    ``MAX_ATTEMPTS`` are spent the trajectory is stored without calling the judge."""
    spans = [
        span
        async for span in Span.objects.filter(trace_id=trace_id).order_by("start_time_ns")[
            :TRACE_SPAN_CAP
        ]
    ]
    if not is_ready(spans):
        return False
    root = roots_of(spans)[0]
    attempts = root.eval_attributes.get("trajectory_attempts", 0)
    spent = attempts >= MAX_ATTEMPTS
    if not spent:
        attempts += 1
        if not await store(root, {**root.eval_attributes, "trajectory_attempts": attempts}):
            return False
    judged = dict(root.eval_attributes)
    await trajectory_factory.get_processor(spans).evaluate(
        spans, judge=not spent, last_attempt=attempts >= MAX_ATTEMPTS
    )
    trajectory = root.eval_attributes["trajectory"]
    if not await store(root, root.eval_attributes, current=judged):
        return False
    await EvalScore.objects.acreate(
        project_id=root.project_id,
        source_type=EvalScore.SourceType.TRAJECTORY,
        source_id=trace_id,
        score=trajectory["score"],
        metrics=trajectory["metrics"],
    )
    return True


async def score_traces(trace_ids: list[str]) -> int:
    scored = 0
    for trace_id in trace_ids:
        try:
            scored += await score_trace(trace_id)
        except Exception as exc:  # noqa: BLE001 — the sweep retries an unscored trajectory
            logger.warning("Scoring trajectory %s failed: %s", trace_id, exc)
    return scored


def unscored_trajectory_ids() -> list[str]:
    return list(
        Span.objects.filter(
            parent_span_id__isnull=True,
            eval_attributes__has_key="score",
            received_at__gte=timezone.now() - SWEEP_WINDOW,
        )
        .exclude(eval_attributes__has_key="trajectory")
        .order_by("received_at")
        .values_list("trace_id", flat=True)[:SWEEP_LIMIT]
    )
