import asyncio
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean, median

from innermind.evals import JevEvaluator
from innermind.innermind import Span
from innermind.utils import (
    FAIL_BELOW,
    REASON_BELOW,
    first_text,
    group_by,
    is_failing,
    read_input,
    read_output,
    requested_tools,
    roots_of,
    score_of,
    scores_of,
    spikes,
    started_at,
    tool_name,
)

logger = logging.getLogger(__name__)


class Signal:
    async def check(self, spans: list[Span]) -> str | None: ...


class LowScoresSignal(Signal):
    run = 5

    async def check(self, spans: list[Span]) -> str | None:
        recent = scores_of(spans)[-self.run :]
        if len(recent) == self.run and all(score < FAIL_BELOW for score in recent):
            scores = ", ".join(f"{score:.2f}" for score in recent)
            return f"last {self.run} scored spans below {FAIL_BELOW}: {scores}"
        return None


class ScoreDropSignal(Signal):
    window = 5
    drop = 0.2

    async def check(self, spans: list[Span]) -> str | None:
        scores = scores_of(spans)
        if len(scores) < 2 * self.window:
            return None
        before, recent = mean(scores[: -self.window]), mean(scores[-self.window :])
        if before - recent >= self.drop:
            return f"mean score fell from {before:.2f} to {recent:.2f}"
        return None


class RepeatedFailureSignal(Signal):
    times = 3

    async def check(self, spans: list[Span]) -> str | None:
        failures = Counter(
            metric["expectation"]
            for span in spans
            for result in span.eval_attributes.get("evaluators", {}).values()
            for metric in result["metrics"]
            if metric["verdict"] == "no"
        )
        repeated = [
            f'"{expectation}" ({n}x)' for expectation, n in failures.items() if n >= self.times
        ]
        if repeated:
            return "expectations failing repeatedly: " + ", ".join(repeated)
        return None


class HiddenFailureSignal(Signal):
    async def check(self, spans: list[Span]) -> str | None:
        hidden = []
        for trace in group_by(spans, "trace_id").values():
            for root in roots_of(trace):
                if (score_of(root) or 0) >= REASON_BELOW:
                    hidden += [span.name for span in trace if span.parent_id and is_failing(span)]
        if hidden:
            return "failing steps under passing answers: " + ", ".join(hidden)
        return None


class UnscorableSignal(Signal):
    rate = 0.5

    async def check(self, spans: list[Span]) -> str | None:
        evaluated = [span for span in spans if "score" in span.eval_attributes]
        unscored = [span for span in evaluated if score_of(span) is None]
        if evaluated and len(unscored) / len(evaluated) >= self.rate:
            return f"{len(unscored)} of {len(evaluated)} evaluated spans have no score"
        return None


class ErrorsSignal(Signal):
    async def check(self, spans: list[Span]) -> str | None:
        failed = [span.name for span in spans if span.status == "error"]
        if failed:
            return f"{len(failed)} of {len(spans)} spans errored: " + ", ".join(failed)
        return None


class ToolFailureSignal(Signal):
    min_calls = 3
    rate = 0.5

    async def check(self, spans: list[Span]) -> str | None:
        calls = defaultdict(list)
        for span in spans:
            if span.kind == "tool":
                calls[tool_name(span)].append(span)
        failing = []
        for name, group in calls.items():
            errored = sum(span.status == "error" for span in group)
            if len(group) >= self.min_calls and errored / len(group) >= self.rate:
                failing.append(f"{name} ({errored}/{len(group)})")
        if failing:
            return "tools failing: " + ", ".join(failing)
        return None


class RetryStormSignal(Signal):
    times = 3

    async def check(self, spans: list[Span]) -> str | None:
        calls = defaultdict(list)
        for span in spans:
            if span.parent_id:
                calls[(span.parent_id, span.name)].append(span)
        storms = [
            f"{name} ({len(group)}x)"
            for (_, name), group in calls.items()
            if len(group) >= self.times and any(span.status == "error" for span in group)
        ]
        if storms:
            return "calls retried under one parent: " + ", ".join(storms)
        return None


class SwallowedErrorSignal(Signal):
    async def check(self, spans: list[Span]) -> str | None:
        swallowed = []
        for trace in group_by(spans, "trace_id").values():
            if any(root.status == "ok" for root in roots_of(trace)):
                swallowed += [span.name for span in trace if span.status == "error"]
        if swallowed:
            return "errors inside traces that finished ok: " + ", ".join(swallowed)
        return None


class AbandonedSignal(Signal):
    after = timedelta(minutes=10)

    async def check(self, spans: list[Span]) -> str | None:
        cutoff = datetime.now(UTC) - self.after
        stuck = [
            span.name for span in spans if span.status == "pending" and started_at(span) < cutoff
        ]
        if stuck:
            return f"spans pending for over {self.after}: " + ", ".join(stuck)
        return None


class LoopSignal(Signal):
    times = 3

    async def check(self, spans: list[Span]) -> str | None:
        calls = Counter(
            (span.trace_id, tool_name(span), read_input(span.attributes))
            for span in spans
            if span.kind == "tool"
        )
        loops = [f"{name} ({n}x)" for (_, name, _), n in calls.items() if n >= self.times]
        if loops:
            return "tools called repeatedly with the same input: " + ", ".join(loops)
        return None


class RunawayTraceSignal(Signal):
    factor = 3

    async def check(self, spans: list[Span]) -> str | None:
        sizes = {trace_id: len(trace) for trace_id, trace in group_by(spans, "trace_id").items()}
        if len(sizes) < 3:
            return None
        typical = median(sizes.values())
        runaway = [
            f"{trace_id} ({n})" for trace_id, n in sizes.items() if n > self.factor * typical
        ]
        if runaway:
            return f"traces over {self.factor}x the usual {typical:g} spans: " + ", ".join(runaway)
        return None


class PlanDriftSignal(Signal):
    expectation = "The step works toward the intent."

    async def check(self, spans: list[Span]) -> str | None:
        evaluator = JevEvaluator()
        steps = [span for span in spans if span.parent_id and span.eval_attributes.get("intent")]
        metrics = await asyncio.gather(*(evaluator.score(span, self.expectation) for span in steps))
        drifting = [
            span.name
            for span, metric in zip(steps, metrics, strict=True)
            if metric.score is not None and metric.score < FAIL_BELOW
        ]
        if drifting:
            return "steps drifting from the intent: " + ", ".join(drifting)
        return None


class AbandonedSessionSignal(Signal):
    idle = timedelta(minutes=30)

    async def check(self, spans: list[Span]) -> str | None:
        cutoff = datetime.now(UTC) - self.idle
        abandoned = []
        for session_id, session in group_by(spans, "session_id").items():
            last = session[-1]
            if started_at(last) < cutoff and (last.status == "error" or is_failing(last)):
                abandoned.append(session_id)
        if abandoned:
            return "sessions ended on a failure: " + ", ".join(abandoned)
        return None


class RepeatedInputSignal(Signal):
    async def check(self, spans: list[Span]) -> str | None:
        repeated = []
        for conversation_id, conversation in group_by(spans, "conversation_id").items():
            inputs = [
                " ".join(read_input(root.attributes).lower().split())
                for root in roots_of(conversation)
            ]
            if any(a and a == b for a, b in zip(inputs, inputs[1:], strict=False)):
                repeated.append(conversation_id)
        if repeated:
            return "users repeated their last message: " + ", ".join(repeated)
        return None


class FrustrationSignal(Signal):
    pattern = re.compile(
        r"\b(?:that'?s (?:wrong|not right)|not what i (?:asked|meant|wanted)|no,? i meant"
        r"|you misunderstood|try again|still (?:wrong|broken|not working)|doesn'?t work)\b",
        re.IGNORECASE,
    )

    async def check(self, spans: list[Span]) -> str | None:
        frustrated = [
            conversation_id
            for conversation_id, conversation in group_by(spans, "conversation_id").items()
            if any(
                self.pattern.search(read_input(root.attributes)) for root in roots_of(conversation)
            )
        ]
        if frustrated:
            return "users correcting the agent: " + ", ".join(frustrated)
        return None


class ContextOverflowSignal(Signal):
    turns = 10
    drop = 0.2

    async def check(self, spans: list[Span]) -> str | None:
        overflowing = []
        for conversation_id, conversation in group_by(spans, "conversation_id").items():
            scores = scores_of(roots_of(conversation))
            if len(scores) < self.turns:
                continue
            half = len(scores) // 2
            if mean(scores[:half]) - mean(scores[half:]) >= self.drop:
                overflowing.append(conversation_id)
        if overflowing:
            return "long conversations losing quality: " + ", ".join(overflowing)
        return None


class SlowLLMSignal(Signal):
    factor = 3

    async def check(self, spans: list[Span]) -> str | None:
        slow = spikes(spans, self.factor, "genai.elapsed_seconds")
        if slow:
            return f"LLM calls over {self.factor}x the usual latency: " + ", ".join(slow)
        return None


class TokenSpikeSignal(Signal):
    factor = 3

    async def check(self, spans: list[Span]) -> str | None:
        heavy = spikes(spans, self.factor, "genai.total_tokens", "genai.usage.total_tokens")
        if heavy:
            return f"LLM calls over {self.factor}x the usual tokens: " + ", ".join(heavy)
        return None


class CostSpikeSignal(Signal):
    factor = 3

    async def check(self, spans: list[Span]) -> str | None:
        costly = spikes(spans, self.factor, "genai.cost")
        if costly:
            return f"LLM calls over {self.factor}x the usual cost: " + ", ".join(costly)
        return None


class ModelChangeSignal(Signal):
    drop = 0.2

    async def check(self, spans: list[Span]) -> str | None:
        calls = [
            (span, model)
            for span in spans
            if span.kind == "llm"
            and (model := first_text(span.attributes, "genai.model", "genai.response.model"))
        ]
        changes = [i for i in range(1, len(calls)) if calls[i][1] != calls[i - 1][1]]
        if not changes:
            return None
        i = changes[-1]
        before = scores_of([span for span, _ in calls[:i]])
        after = scores_of([span for span, _ in calls[i:]])
        if before and after and mean(before) - mean(after) >= self.drop:
            return (
                f"score fell from {mean(before):.2f} to {mean(after):.2f} "
                f"after switching {calls[i - 1][1]} to {calls[i][1]}"
            )
        return None


class LeakSignal(Signal):
    patterns = {
        "api key": re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{20,}|phx_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36})\b"
        ),
        "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b"),
        "card number": re.compile(r"\b(?:\d[ -]?){13,15}\d\b"),
    }

    async def check(self, spans: list[Span]) -> str | None:
        leaks = [
            f"{span.name} ({kind})"
            for span in spans
            for kind, pattern in self.patterns.items()
            if pattern.search(read_output(span.attributes))
        ]
        if leaks:
            return "sensitive data in outputs: " + ", ".join(leaks)
        return None


class RefusalSignal(Signal):
    pattern = re.compile(
        r"\b(?:i can(?:'|no)?t (?:help|assist)|i(?:'m| am) (?:not able|unable) to"
        r"|i won'?t be able to)\b",
        re.IGNORECASE,
    )

    async def check(self, spans: list[Span]) -> str | None:
        refused = [span.name for span in spans if self.pattern.search(read_output(span.attributes))]
        if refused:
            return "outputs refusing the task: " + ", ".join(refused)
        return None


class BrokenJsonSignal(Signal):
    async def check(self, spans: list[Span]) -> str | None:
        broken = []
        for span in spans:
            output = read_output(span.attributes)
            if output.startswith(("{", "[")):
                try:
                    json.loads(output)
                except json.JSONDecodeError:
                    broken.append(span.name)
        if broken:
            return "outputs with malformed JSON: " + ", ".join(broken)
        return None


class HallucinatedToolSignal(Signal):
    async def check(self, spans: list[Span]) -> str | None:
        ran = {tool_name(span) for span in spans if span.kind == "tool"}
        requested = {
            name
            for span in spans
            if span.kind == "llm"
            for name in requested_tools(read_output(span.attributes))
        }
        unknown = sorted(name for name in requested - ran if name)
        if unknown:
            return "LLM requested tools that never ran: " + ", ".join(unknown)
        return None


class SignalFactory:
    signals = {
        "low_scores": LowScoresSignal,
        "score_drop": ScoreDropSignal,
        "repeated_failure": RepeatedFailureSignal,
        "hidden_failure": HiddenFailureSignal,
        "unscorable": UnscorableSignal,
        "errors": ErrorsSignal,
        "tool_failure": ToolFailureSignal,
        "retry_storm": RetryStormSignal,
        "swallowed_error": SwallowedErrorSignal,
        "abandoned": AbandonedSignal,
        "loop": LoopSignal,
        "runaway_trace": RunawayTraceSignal,
        "plan_drift": PlanDriftSignal,
        "abandoned_session": AbandonedSessionSignal,
        "repeated_input": RepeatedInputSignal,
        "frustration": FrustrationSignal,
        "context_overflow": ContextOverflowSignal,
        "slow_llm": SlowLLMSignal,
        "token_spike": TokenSpikeSignal,
        "cost_spike": CostSpikeSignal,
        "model_change": ModelChangeSignal,
        "leak": LeakSignal,
        "refusal": RefusalSignal,
        "broken_json": BrokenJsonSignal,
        "hallucinated_tool": HallucinatedToolSignal,
    }

    def get_signal(self, signal_type: str) -> Signal:
        if signal_type not in self.signals:
            raise ValueError(f"Unknown signal type: {signal_type}")
        return self.signals[signal_type]()


signal_factory = SignalFactory()


async def create_signal_from_spans(spans: list[Span], signal_type: str, scope: str) -> str | None:
    signal = signal_factory.get_signal(signal_type)
    message = await signal.check(sorted(spans, key=lambda span: span.start))
    if message:
        print(f"signal {signal_type} for {scope}: {message}")
    return message
