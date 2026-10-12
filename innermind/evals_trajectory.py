import asyncio
import json
from collections import Counter
from contextlib import suppress
from dataclasses import asdict

from innermind.evals import JevEvaluator
from innermind.innermind import Span
from innermind.utils import (
    FAIL_BELOW,
    REASON_BELOW,
    Metric,
    is_failing,
    number,
    read_input,
    read_output,
    roots_of,
    score_of,
    tool_name,
)

JEV_STATE_LIMIT = 30_000


def is_in_order(expected: list[str], actual: list[str]) -> bool:
    calls = iter(actual)
    return all(name in calls for name in expected)


def read_reference(attributes: dict) -> list[str]:
    value = attributes.get("overmind.reference_trajectory", attributes.get("reference_trajectory"))
    if isinstance(value, str):
        with suppress(json.JSONDecodeError):
            value = json.loads(value)
    if not isinstance(value, (list, tuple)):
        return []
    return [call.get("name", "") if isinstance(call, dict) else str(call) for call in value]


class TrajectoryEvaluator:
    async def score(self, spans: list[Span]) -> Metric | None: ...


class OutcomeEvaluator(TrajectoryEvaluator):
    expectation = "The agent achieves the intent."

    async def score(self, spans: list[Span]) -> Metric | None:
        score = score_of(roots_of(spans)[0])
        if score is None:
            return Metric(self.expectation, error="root span has no score")
        return Metric(self.expectation, score=score)


class WeakestStepEvaluator(TrajectoryEvaluator):
    expectation = "Every step meets its expectations."

    async def score(self, spans: list[Span]) -> Metric | None:
        steps = [span for span in spans if span.parent_id and score_of(span) is not None]
        if not steps:
            return None
        weakest = min(steps, key=score_of)
        return Metric(
            self.expectation, score=score_of(weakest), reason=f"weakest step: {weakest.name}"
        )


class EfficiencyEvaluator(TrajectoryEvaluator):
    expectation = "The agent makes no redundant calls and recovers from every error."

    async def score(self, spans: list[Span]) -> Metric | None:
        calls = Counter(
            (tool_name(span), read_input(span.attributes))
            for span in spans
            if span.kind == "tool" and span.status == "ok"
        )
        redundant = sum(n - 1 for n in calls.values())
        unrecovered = sum(
            span.status == "error"
            and not any(
                later.parent_id == span.parent_id
                and later.name == span.name
                and later.status == "ok"
                for later in spans[i + 1 :]
            )
            for i, span in enumerate(spans)
        )
        llm_calls = [span.attributes for span in spans if span.kind == "llm"]
        tokens = sum(
            number(a, "genai.total_tokens", "genai.usage.total_tokens") or 0 for a in llm_calls
        )
        cost = sum(number(a, "genai.cost") or 0 for a in llm_calls)
        seconds = sum(number(a, "genai.elapsed_seconds") or 0 for a in llm_calls)
        return Metric(
            self.expectation,
            score=max(0.0, 1 - (redundant + unrecovered) / len(spans)),
            reason=(
                f"{redundant} redundant calls, {unrecovered} unrecovered errors, "
                f"{tokens:g} tokens, ${cost:g}, {seconds:g}s in LLM calls"
            ),
        )


class PathJudgeEvaluator(TrajectoryEvaluator):
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
                "kind": span.kind,
                "status": span.status,
                "score": score_of(span),
                "input": read_input(span.attributes)[: self.step_chars],
                "output": read_output(span.attributes)[: self.step_chars],
            }
            for span in spans
            if span is not root
        ]
        while True:
            state = {
                "intent": intent,
                "expectation": self.expectation,
                "input": json.dumps(steps, ensure_ascii=False),
                "output": read_output(root.attributes),
            }
            # Keep the first and last steps; the middle of a long run is the least telling.
            if len(json.dumps(state).encode()) <= JEV_STATE_LIMIT or len(steps) <= 2:
                break
            del steps[len(steps) // 2]
        evaluator = JevEvaluator()
        metric = await evaluator.judge(state, self.expectation)
        if metric.score is not None and metric.score < REASON_BELOW:
            metric.reason = await evaluator.explain(state, metric)
        return metric


class ReferenceEvaluator(TrajectoryEvaluator):
    expectation = "The agent calls the reference tools in order."

    async def score(self, spans: list[Span]) -> Metric | None:
        expected = read_reference(roots_of(spans)[0].attributes)
        if not expected:
            return None
        actual = [tool_name(span) for span in spans if span.kind == "tool"]
        recall = sum(name in actual for name in expected) / len(expected)
        precision = sum(name in expected for name in actual) / len(actual) if actual else 0.0
        in_order = is_in_order(expected, actual)
        return Metric(
            self.expectation,
            score=(recall + precision) / 2,
            verdict="in order" if in_order else "out of order",
            reason=f"{recall:.0%} of reference tools called, {precision:.0%} of calls expected",
        )


class TrajectoryProcessor:
    def get_evaluators(self) -> list[TrajectoryEvaluator]:
        raise NotImplementedError("Subclasses must implement this method")

    async def evaluate(self, spans: list[Span]) -> Span:
        root = roots_of(spans)[0]
        metrics = await asyncio.gather(
            *(evaluator.score(spans) for evaluator in self.get_evaluators())
        )
        outcome = score_of(root)
        root.eval_attributes["trajectory"] = {
            "span_count": len(spans),
            "passed": None
            if outcome is None
            else outcome >= FAIL_BELOW and not any(map(is_failing, spans)),
            "metrics": [asdict(metric) for metric in metrics if metric is not None],
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


async def score_trajectory(spans: list[Span]) -> Span:
    """
    scores a trace's trajectory and stores it on the root span, as new spans are added the trajectory is scored again

    this doesnt save the score to the database, it just returns the updated root span
    """
    spans = sorted(spans, key=lambda span: span.start)
    processor = trajectory_factory.get_processor(spans)
    return await processor.evaluate(spans)
