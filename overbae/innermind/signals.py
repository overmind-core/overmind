"""Automatic checks over recent conversations and traces, with per-project overrides.

A check returns a ``SignalFinding``: the headline, the rule it crossed, the measured
values, and the offending spans with a reason each. The runner stores all of it on
``SignalEvent.details`` so a reviewer or a downstream workflow reads the evidence
without re-deriving it from the traces.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from statistics import mean, median
from typing import Any, ClassVar

from django.db.models import Func, IntegerField, Sum, TextField, Window
from django.db.models.functions import Cast, Coalesce
from django.utils import timezone

from overbae.innermind import evidence
from overbae.innermind.evals import JevEvaluator
from overbae.innermind.metrics import (
    FAIL_BELOW,
    REASON_BELOW,
    group_by,
    is_failing,
    is_final,
    roots_of,
    score_of,
)
from overbae.innermind.span_processor import MAX_ATTEMPTS
from overbae.models import SignalEvent, SignalSubscription, Span

logger = logging.getLogger(__name__)

WINDOW = timedelta(days=1)
PROJECT_SPAN_CAP = 20_000
SIGNAL_BATCH_BYTES = 8 * 1024 * 1024
SIGNAL_BATCH_ROWS = 256
EVIDENCE_SPANS = 10
EVIDENCE_TEXT = 500

SENSITIVE_PATTERNS: dict[str, re.Pattern] = {
    "api key": re.compile(
        r"\b(?:sk-[A-Za-z0-9_-]{20,}|phx_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36})\b"
    ),
    "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b"),
    "card number": re.compile(r"\b(?:\d[ -]?){13,15}\d\b"),
}


class SignalInputTooLargeError(ValueError):
    pass


def span_payload_bytes():
    fields = (
        "attributes",
        "eval_attributes",
        "events",
        "links",
        "resource_attrs",
        "usage",
        "feedback_score",
        "status_message",
        "name",
        "operation",
        "service_name",
        "scope_name",
        "scope_version",
    )
    return sum(
        Coalesce(
            Func(Cast(field, TextField()), function="OCTET_LENGTH", output_field=IntegerField()), 0
        )
        for field in fields
    )


def started_at(span: Span) -> datetime:
    return datetime.fromtimestamp(span.start_time_ns / 1e9, tz=UTC)


def valid_card_number(value: str) -> bool:
    digits = [int(char) for char in value if char.isdigit()]
    total = 0
    for offset, digit in enumerate(reversed(digits)):
        if offset % 2:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def sensitive_matches(text: str):
    for kind, pattern in SENSITIVE_PATTERNS.items():
        for match in pattern.finditer(text):
            if kind != "card number" or valid_card_number(match.group()):
                yield kind, match


def redact(text: str) -> str:
    """Signal details outlive the span, so they never copy a secret out of it."""
    for kind, match in sorted(
        sensitive_matches(text), key=lambda item: item[1].start(), reverse=True
    ):
        text = text[: match.start()] + f"[{kind}]" + text[match.end() :]
    return text


def clip(text: str) -> str:
    return redact(text)[:EVIDENCE_TEXT]


def failed_metrics(span: Span) -> list[dict]:
    return [
        {
            "name": name,
            "expectation": metric.get("expectation", ""),
            "score": metric.get("score"),
            "verdict": metric.get("verdict", ""),
            "reason": clip(metric.get("reason", "")),
        }
        for name, metric in (span.eval_attributes or {}).get("metrics", {}).items()
        if metric.get("verdict") == "no"
        or (metric.get("score") is not None and metric["score"] < FAIL_BELOW)
    ]


def span_evidence(span: Span, trace: dict[str, Span], reason: str) -> dict:
    attributes = span.eval_attributes or {}
    return {
        "span_id": span.span_id,
        "trace_id": span.trace_id,
        "name": span.name,
        "span_type": span.span_type,
        "status": evidence.status(span),
        "status_message": clip(span.status_message or ""),
        "model": evidence.model_name(span) or None,
        "tool": evidence.tool_name(span) if evidence.is_tool(span) else None,
        "duration_ms": round(span.duration_ns / 1_000_000, 1),
        "tokens": evidence.tokens(span),
        "cost": evidence.cost(span),
        "input": clip(evidence.read_input(span)),
        "output": clip(evidence.read_output(span)),
        "intent": clip(evidence.read_intent(span, trace)),
        "score": score_of(span),
        "drift": attributes.get("drift"),
        "failed_metrics": failed_metrics(span),
        "reason": reason,
    }


def spikes(spans: list[Span], factor: float, measure) -> tuple[list[tuple[Span, float]], float]:
    """LLM calls whose measure exceeds ``factor`` times the median of the calls measured."""
    values = [(span, value) for span in spans if evidence.is_llm(span) and (value := measure(span))]
    if len(values) < 3:
        return [], 0.0
    typical = median(value for _, value in values)
    return [
        (span, value) for span, value in values if typical and value > factor * typical
    ], typical


def scored(spans: list[Span]) -> list[tuple[Span, float]]:
    return [(span, score) for span in spans if (score := score_of(span)) is not None]


@dataclass
class SignalFinding:
    message: str
    why: str
    # Offending spans in group order, each with why it counts.
    spans: list[tuple[Span, str]] = field(default_factory=list)
    measurements: dict[str, Any] = field(default_factory=dict)


class Signal:
    name: ClassVar[str]
    description: ClassVar[str]
    default_enabled: ClassVar[bool] = True
    # Class attributes a subscription's ``config`` may override.
    params: ClassVar[tuple[str, ...]] = ()
    # The first action item on every event of this signal.
    remedy: ClassVar[str] = ""
    # Score-based signals point the reviewer at the failing eval rows.
    scored: ClassVar[bool] = False
    # Ranks insights: "high" for exposed data, failures and falling quality, "low" for
    # latency, spend and scoring gaps.
    severity: ClassVar[str] = "medium"

    def __init__(self, project_id: str, config: dict | None = None):
        self.project_id = str(project_id)
        for key, value in (config or {}).items():
            if key in self.params:
                setattr(self, key, value)

    async def evaluate(self, spans: list[Span]) -> SignalFinding | None: ...

    def parameters(self) -> dict[str, Any]:
        return {param: getattr(self, param) for param in self.params}

    def actions(self, trace_ids: list[str], capability_id) -> list[dict[str, str]]:
        traces = f"{len(trace_ids)} trace{'s' if len(trace_ids) != 1 else ''}"
        actions = [{"kind": "fix", "label": self.remedy}] if self.remedy else []
        actions.append({"kind": "inspect_traces", "label": f"Inspect {traces}"})
        actions.append({"kind": "create_dataset", "label": f"Create a dataset from {traces}"})
        if self.scored:
            actions.append({"kind": "review_evaluations", "label": "Review the failing eval rows"})
        if capability_id:
            actions.append(
                {"kind": "open_optimiser", "label": "Optimise the capability on that dataset"}
            )
        return actions

    def details(self, finding: SignalFinding, group: list[Span], key: str, capability_id) -> dict:
        offending = [span for span, _ in finding.spans]
        group_trace_ids = list(dict.fromkeys(span.trace_id for span in group))
        trace_ids = list(dict.fromkeys(span.trace_id for span in offending)) or group_trace_ids
        trace = {span.span_id: span for span in group}
        return {
            "spans": len(group),
            "group": key,
            "trace_ids": trace_ids,
            "group_trace_ids": group_trace_ids,
            "span_ids": [span.span_id for span in offending],
            "why": finding.why,
            "parameters": self.parameters(),
            "measurements": finding.measurements,
            "evidence": [
                span_evidence(span, trace, reason)
                for span, reason in finding.spans[:EVIDENCE_SPANS]
            ],
            "evidence_truncated": len(finding.spans) > EVIDENCE_SPANS,
            "recommended_actions": self.actions(trace_ids, capability_id),
        }


class LowScoresSignal(Signal):
    name = "low_scores"
    severity = "high"
    description = "The last scored spans all fall below the failing score."
    params = ("run",)
    remedy = "Fix the behaviour the failing eval rows describe"
    scored = True
    run = 5

    async def evaluate(self, spans):
        recent = scored(spans)[-self.run :]
        if len(recent) == self.run and all(score < FAIL_BELOW for _, score in recent):
            scores = ", ".join(f"{score:.2f}" for _, score in recent)
            return SignalFinding(
                message=f"last {self.run} scored spans below {FAIL_BELOW}: {scores}",
                why=f"Each of the last {self.run} scored spans scored below the failing score "
                f"{FAIL_BELOW}.",
                spans=[(span, f"scored {score:.2f}") for span, score in recent],
                measurements={"scores": [round(score, 3) for _, score in recent]},
            )
        return None


class ScoreDropSignal(Signal):
    name = "score_drop"
    severity = "high"
    description = "The mean of the most recent scores falls against the earlier mean."
    params = ("window", "drop")
    remedy = "Compare the recent spans against the earlier ones for a prompt, model or data change"
    scored = True
    window = 5
    drop = 0.2

    async def evaluate(self, spans):
        pairs = scored(spans)
        if len(pairs) < 2 * self.window:
            return None
        earlier, recent = pairs[: -self.window], pairs[-self.window :]
        before = mean(score for _, score in earlier)
        after = mean(score for _, score in recent)
        if before - after >= self.drop:
            return SignalFinding(
                message=f"mean score fell from {before:.2f} to {after:.2f} over the last "
                f"{self.window} scored spans",
                why=f"The mean of the last {self.window} scored spans ({after:.2f}) is "
                f"{before - after:.2f} below the mean of the {len(earlier)} before them "
                f"({before:.2f}); the threshold is {self.drop}.",
                spans=[(span, f"scored {score:.2f}") for span, score in recent],
                measurements={
                    "before_mean": round(before, 3),
                    "recent_mean": round(after, 3),
                    "drop": round(before - after, 3),
                    "before_count": len(earlier),
                },
            )
        return None


class RepeatedFailureSignal(Signal):
    name = "repeated_failure"
    severity = "high"
    description = "One expectation fails repeatedly."
    params = ("times",)
    remedy = "Fix the behaviour behind the repeated expectation"
    scored = True
    times = 3

    async def evaluate(self, spans):
        failures = defaultdict(list)
        for span in spans:
            for name, metric in span.eval_attributes.get("metrics", {}).items():
                if metric.get("verdict") == "no":
                    failures[metric.get("expectation", name)].append(span)
        repeated = {text: group for text, group in failures.items() if len(group) >= self.times}
        if not repeated:
            return None
        return SignalFinding(
            message="expectations failing repeatedly: "
            + ", ".join(f'"{text}" ({len(group)}x)' for text, group in repeated.items()),
            why=f"Each listed expectation was judged not met on at least {self.times} spans.",
            spans=[
                (span, f'failed "{text}"') for text, group in repeated.items() for span in group
            ],
            measurements={"failures": {text: len(group) for text, group in repeated.items()}},
        )


class HiddenFailureSignal(Signal):
    name = "hidden_failure"
    severity = "high"
    description = "A step fails under a root answer that passes."
    remedy = "Fix the failing step; the passing answer hides it"
    scored = True

    async def evaluate(self, spans):
        hidden = []
        for trace in group_by(spans, lambda span: span.trace_id).values():
            for root in roots_of(trace):
                root_score = score_of(root) or 0
                if root_score >= REASON_BELOW:
                    hidden += [
                        (
                            span,
                            f"scored {score_of(span):.2f} under {root.name} scoring "
                            f"{root_score:.2f}",
                        )
                        for span in trace
                        if not span.is_root and is_failing(span)
                    ]
        if hidden:
            return SignalFinding(
                message="failing steps under passing answers: "
                + ", ".join(span.name for span, _ in hidden),
                why=f"These steps scored below {FAIL_BELOW} inside traces whose root scored at "
                f"least {REASON_BELOW}.",
                spans=hidden,
                measurements={"hidden_steps": len(hidden)},
            )
        return None


class UnscorableSignal(Signal):
    name = "unscorable"
    severity = "low"
    description = "Expectations error on spans and leave them without a score."
    params = ("rate",)
    remedy = "Check the eval rows and span payloads these spans could not be scored on"
    rate = 0.5

    async def evaluate(self, spans):
        evaluated = [span for span in spans if span.eval_attributes.get("metrics")]
        unscored = [
            (span, errors[0])
            for span in evaluated
            if score_of(span) is None
            and (
                errors := [
                    metric["error"]
                    for metric in span.eval_attributes["metrics"].values()
                    if metric.get("error")
                ]
            )
        ]
        if evaluated and len(unscored) / len(evaluated) >= self.rate:
            return SignalFinding(
                message=f"{len(unscored)} of {len(evaluated)} evaluated spans failed scoring",
                why=f"On {len(unscored) / len(evaluated):.0%} of the spans with expectations, "
                f"no expectation produced a score and at least one errored; the threshold is "
                f"{self.rate:.0%}.",
                spans=[(span, clip(error)) for span, error in unscored],
                measurements={
                    "evaluated": len(evaluated),
                    "unscored": len(unscored),
                    "rate": round(len(unscored) / len(evaluated), 3),
                },
            )
        return None


class ErrorsSignal(Signal):
    name = "errors"
    severity = "high"
    description = "Spans end with an error status."
    remedy = "Fix the errors named in the span status messages"

    async def evaluate(self, spans):
        failed = [span for span in spans if evidence.status(span) == "error"]
        if failed:
            return SignalFinding(
                message=f"{len(failed)} of {len(spans)} spans errored: "
                + ", ".join(span.name for span in failed),
                why="These spans ended with an OpenTelemetry error status.",
                spans=[(span, clip(span.status_message) or "error status") for span in failed],
                measurements={"errored": len(failed), "spans": len(spans)},
            )
        return None


class ToolFailureSignal(Signal):
    name = "tool_failure"
    severity = "high"
    description = "A tool errors on a large share of its calls."
    params = ("min_calls", "rate")
    remedy = "Fix or guard the failing tool"
    min_calls = 3
    rate = 0.5

    async def evaluate(self, spans):
        calls = defaultdict(list)
        for span in spans:
            if evidence.is_tool(span):
                calls[evidence.tool_name(span)].append(span)
        failing = {}
        for name, group in calls.items():
            errored = [span for span in group if evidence.status(span) == "error"]
            if len(group) >= self.min_calls and len(errored) / len(group) >= self.rate:
                failing[name] = (errored, len(group))
        if not failing:
            return None
        return SignalFinding(
            message="tools failing: "
            + ", ".join(f"{name} ({len(errored)}/{n})" for name, (errored, n) in failing.items()),
            why=f"Each listed tool errored on at least {self.rate:.0%} of at least "
            f"{self.min_calls} calls.",
            spans=[
                (span, clip(span.status_message) or f"{name} errored")
                for name, (errored, _) in failing.items()
                for span in errored
            ],
            measurements={
                "tools": {
                    name: {
                        "calls": n,
                        "errored": len(errored),
                        "rate": round(len(errored) / n, 3),
                    }
                    for name, (errored, n) in failing.items()
                }
            },
        )


class RetryStormSignal(Signal):
    name = "retry_storm"
    description = "One call is retried repeatedly under the same parent after an error."
    params = ("times",)
    remedy = "Bound the retries or fix the error that triggers them"
    times = 3

    async def evaluate(self, spans):
        calls = defaultdict(list)
        for span in spans:
            if not span.is_root:
                calls[(span.parent_span_id, span.name)].append(span)
        storms = {
            name: group
            for (_, name), group in calls.items()
            if len(group) >= self.times and any(evidence.status(s) == "error" for s in group)
        }
        if not storms:
            return None
        return SignalFinding(
            message="calls retried under one parent: "
            + ", ".join(f"{name} ({len(group)}x)" for name, group in storms.items()),
            why=f"Each listed call ran at least {self.times} times under one parent and at "
            "least one attempt errored.",
            spans=[
                (span, f"attempt {index} of {len(group)}, {evidence.status(span)}")
                for group in storms.values()
                for index, span in enumerate(group, 1)
            ],
            measurements={"calls": {name: len(group) for name, group in storms.items()}},
        )


class SwallowedErrorSignal(Signal):
    name = "swallowed_error"
    severity = "high"
    description = "A span errors inside a trace whose root finishes ok."
    remedy = "Surface or handle the error the root hides"

    async def evaluate(self, spans):
        swallowed = []
        for trace in group_by(spans, lambda span: span.trace_id).values():
            ok_roots = [root for root in roots_of(trace) if evidence.status(root) == "ok"]
            if ok_roots:
                swallowed += [
                    (span, f"errored under {ok_roots[0].name}, which finished ok")
                    for span in trace
                    if evidence.status(span) == "error"
                ]
        if swallowed:
            return SignalFinding(
                message="errors inside traces that finished ok: "
                + ", ".join(span.name for span, _ in swallowed),
                why="These spans errored inside traces whose root span finished ok.",
                spans=swallowed,
                measurements={"swallowed": len(swallowed)},
            )
        return None


class AbandonedSignal(Signal):
    name = "abandoned"
    description = "A trace has no root span long after its first span arrived."
    params = ("minutes",)
    remedy = "Check that the root span ends and is exported"
    minutes = 10

    async def evaluate(self, spans):
        cutoff = timezone.now() - timedelta(minutes=self.minutes)
        stuck = [
            min(trace, key=lambda span: span.received_at)
            for trace in group_by(spans, lambda span: span.trace_id).values()
            if not roots_of(trace) and min(span.received_at for span in trace) < cutoff
        ]
        if stuck:
            return SignalFinding(
                message=f"traces without a root after {self.minutes} minutes: "
                + ", ".join(span.trace_id for span in stuck),
                why=f"No root span arrived within {self.minutes} minutes of the trace's first "
                "span.",
                spans=[(span, "first span of a trace with no root") for span in stuck],
                measurements={"traces": len(stuck)},
            )
        return None


class LoopSignal(Signal):
    name = "loop"
    description = "A tool is called repeatedly with the same input in one trace."
    params = ("times",)
    remedy = "Stop the agent repeating the same tool call"
    times = 3

    async def evaluate(self, spans):
        calls = defaultdict(list)
        for span in spans:
            if evidence.is_tool(span):
                key = (span.trace_id, evidence.tool_name(span), evidence.read_input(span))
                calls[key].append(span)
        loops = {key: group for key, group in calls.items() if len(group) >= self.times}
        if not loops:
            return None
        return SignalFinding(
            message="tools called repeatedly with the same input: "
            + ", ".join(f"{name} ({len(group)}x)" for (_, name, _), group in loops.items()),
            why=f"Each listed tool ran at least {self.times} times with identical input in "
            "one trace.",
            spans=[
                (span, f"call {index} of {len(group)} with the same input")
                for group in loops.values()
                for index, span in enumerate(group, 1)
            ],
            measurements={"calls": {name: len(group) for (_, name, _), group in loops.items()}},
        )


class RunawayTraceSignal(Signal):
    name = "runaway_trace"
    description = "A trace has many times the usual number of spans."
    params = ("factor",)
    remedy = "Bound the steps the agent can take"
    factor = 3

    async def evaluate(self, spans):
        traces = group_by(spans, lambda span: span.trace_id)
        if len(traces) < 3:
            return None
        typical = median(len(trace) for trace in traces.values())
        runaway = {
            trace_id: trace
            for trace_id, trace in traces.items()
            if len(trace) > self.factor * typical
        }
        if not runaway:
            return None
        return SignalFinding(
            message=f"traces over {self.factor}x the usual {typical:g} spans: "
            + ", ".join(f"{trace_id} ({len(trace)})" for trace_id, trace in runaway.items()),
            why=f"These traces have more than {self.factor} times the median {typical:g} "
            "spans per trace.",
            spans=[
                ((roots_of(trace) or trace)[0], f"{len(trace)} spans vs {typical:g} typical")
                for trace in runaway.values()
            ],
            measurements={
                "median_spans": typical,
                "traces": {trace_id: len(trace) for trace_id, trace in runaway.items()},
            },
        )


class PlanDriftSignal(Signal):
    """Each scored step is judged once and its verdict stored as ``eval_attributes.drift``;
    a judge that keeps failing stops after ``MAX_ATTEMPTS``."""

    name = "plan_drift"
    default_enabled = False
    description = "Jev judges steps as not working toward the intent."
    remedy = "Keep the drifting steps on the stated intent"
    scored = True
    expectation = "The step works toward the intent."

    async def evaluate(self, spans):
        steps = [
            span
            for span in spans
            if not span.is_root and is_final(span) and span.eval_attributes.get("intent")
        ]
        unjudged = [
            span
            for span in steps
            if "drift" not in span.eval_attributes
            and span.eval_attributes.get("drift_attempts", 0) < MAX_ATTEMPTS
        ]
        outcomes = await asyncio.gather(*map(self.judge, unjudged), return_exceptions=True)
        for span, outcome in zip(unjudged, outcomes, strict=True):
            if isinstance(outcome, BaseException) and not isinstance(outcome, Exception):
                raise outcome
            await self.record(span, outcome)
        drifting = [
            (span, score)
            for span in steps
            if (score := span.eval_attributes.get("drift")) is not None and score < FAIL_BELOW
        ]
        if drifting:
            return SignalFinding(
                message="steps drifting from the intent: "
                + ", ".join(span.name for span, _ in drifting),
                why=f'Jev scored these steps below {FAIL_BELOW} on "{self.expectation}"',
                spans=[(span, f"drift score {score:.2f}") for span, score in drifting],
                measurements={"drifting": len(drifting), "judged": len(steps)},
            )
        return None

    async def judge(self, span: Span) -> float | None:
        judge = JevEvaluator(self.project_id)
        state = judge.evidence(span, self.expectation)
        if "output" not in state:
            return None
        return (await judge.judge(state, self.expectation)).score

    async def record(self, span: Span, outcome: float | None | Exception) -> None:
        attrs = span.eval_attributes
        if isinstance(outcome, Exception):
            logger.warning("Plan drift judge failed on span %s: %s", span.span_id, outcome)
            judged = {**attrs, "drift_attempts": attrs.get("drift_attempts", 0) + 1}
        else:
            judged = {**attrs, "drift": outcome}
        # Matching the stored attributes keeps a concurrent re-ingest's reset.
        if await Span.objects.filter(span_id=span.span_id, eval_attributes=attrs).aupdate(
            eval_attributes=judged
        ):
            span.eval_attributes = judged


class AbandonedSessionSignal(Signal):
    name = "abandoned_session"
    description = "A conversation goes idle after a failing or errored span."
    params = ("minutes",)
    remedy = "Fix the failure the user left on"
    minutes = 30

    async def evaluate(self, spans):
        if not spans or not spans[-1].conversation_id:
            return None
        last = spans[-1]
        idle = datetime.now(UTC) - started_at(last)
        errored = evidence.status(last) == "error"
        if idle > timedelta(minutes=self.minutes) and (errored or is_failing(last)):
            return SignalFinding(
                message=f"conversation ended on a failure: {last.name}",
                why=f"The conversation's last span {'errored' if errored else 'failed'} and "
                f"nothing followed for at least {self.minutes} minutes.",
                spans=[(last, "errored" if errored else f"scored {score_of(last):.2f}")],
                measurements={"idle_minutes": round(idle.total_seconds() / 60, 1)},
            )
        return None


class RepeatedInputSignal(Signal):
    name = "repeated_input"
    description = "The user repeats their previous message."
    remedy = "Fix the answer the user had to ask for again"

    async def evaluate(self, spans):
        roots = roots_of(spans)
        inputs = [" ".join(evidence.read_input(root).lower().split()) for root in roots]
        repeats = [
            roots[index + 1]
            for index, (a, b) in enumerate(zip(inputs, inputs[1:], strict=False))
            if a and a == b
        ]
        if repeats:
            return SignalFinding(
                message="the user repeated their last message",
                why="A turn's input matches the previous turn's input.",
                spans=[(root, "repeats the previous message") for root in repeats],
                measurements={"repeats": len(repeats)},
            )
        return None


class FrustrationSignal(Signal):
    name = "frustration"
    description = "The user corrects the agent."
    remedy = "Fix the answer the user corrected"
    pattern = re.compile(
        r"\b(?:that'?s (?:wrong|not right)|not what i (?:asked|meant|wanted)|no,? i meant"
        r"|you misunderstood|try again|still (?:wrong|broken|not working)|doesn'?t work)\b",
        re.IGNORECASE,
    )

    async def evaluate(self, spans):
        corrected = [
            (root, f'matched "{match.group()}"')
            for root in roots_of(spans)
            if (match := self.pattern.search(evidence.read_input(root)))
        ]
        if corrected:
            return SignalFinding(
                message="the user corrected the agent",
                why="A turn's input contains a correction phrase.",
                spans=corrected,
                measurements={"corrections": len(corrected)},
            )
        return None


class ContextOverflowSignal(Signal):
    name = "context_overflow"
    description = "Root scores fall in the second half of a long conversation."
    params = ("turns", "drop")
    remedy = "Trim or summarise the conversation context"
    scored = True
    turns = 10
    drop = 0.2

    async def evaluate(self, spans):
        pairs = scored(roots_of(spans))
        if len(pairs) < self.turns:
            return None
        half = len(pairs) // 2
        before = mean(score for _, score in pairs[:half])
        after = mean(score for _, score in pairs[half:])
        if before - after >= self.drop:
            return SignalFinding(
                message=f"root scores fell from {before:.2f} to {after:.2f} over {len(pairs)} "
                "turns",
                why=f"The second half of {len(pairs)} scored turns averages {after:.2f}, "
                f"{before - after:.2f} below the first half; the threshold is {self.drop}.",
                spans=[
                    (span, f"turn {half + i} scored {s:.2f}")
                    for i, (span, s) in enumerate(pairs[half:], 1)
                    if s < before
                ],
                measurements={
                    "first_half_mean": round(before, 3),
                    "second_half_mean": round(after, 3),
                    "turns": len(pairs),
                },
            )
        return None


class SpikeSignal(Signal):
    params = ("factor",)
    factor = 3
    noun: ClassVar[str]
    unit: ClassVar[str]

    @staticmethod
    def measure(span: Span) -> float | None: ...

    async def evaluate(self, spans):
        found, typical = spikes(spans, self.factor, self.measure)
        if not found:
            return None
        return SignalFinding(
            message=f"LLM calls over {self.factor}x the usual {self.noun}: "
            + ", ".join(span.name for span, _ in found),
            why=f"These LLM calls exceed {self.factor} times the median {self.noun} "
            f"({typical:g}{self.unit}) of the calls in the group.",
            spans=[
                (span, f"{value:g}{self.unit} vs {typical:g}{self.unit} median")
                for span, value in found
            ],
            measurements={
                "median": typical,
                "max": max(value for _, value in found),
                "unit": self.unit.strip() or self.noun,
            },
        )


class SlowLLMSignal(SpikeSignal):
    name = "slow_llm"
    severity = "low"
    description = "LLM calls take many times the usual latency."
    remedy = "Check the slow calls' prompt size, model and provider"
    noun = "latency"
    unit = "s"
    measure = staticmethod(evidence.seconds)


class TokenSpikeSignal(SpikeSignal):
    name = "token_spike"
    severity = "low"
    description = "LLM calls use many times the usual tokens."
    remedy = "Check what inflates the heavy calls' prompts or outputs"
    noun = "tokens"
    unit = " tokens"
    measure = staticmethod(evidence.tokens)


class CostSpikeSignal(SpikeSignal):
    name = "cost_spike"
    severity = "low"
    description = "LLM calls cost many times the usual amount."
    remedy = "Check the costly calls' model and token use"
    noun = "cost"
    unit = " USD"
    measure = staticmethod(evidence.cost)


class ModelChangeSignal(Signal):
    name = "model_change"
    description = "Scores fall after the model changes."
    params = ("drop",)
    remedy = "Compare the two models on an eval set before keeping the switch"
    scored = True
    drop = 0.2

    async def evaluate(self, spans):
        calls = [
            (span, model)
            for span in spans
            if evidence.is_llm(span) and (model := evidence.model_name(span))
        ]
        changes = [i for i in range(1, len(calls)) if calls[i][1] != calls[i - 1][1]]
        if not changes:
            return None
        i = changes[-1]
        before = scored([span for span, _ in calls[:i]])
        after = scored([span for span, _ in calls[i:]])
        if not (before and after):
            return None
        before_mean = mean(score for _, score in before)
        after_mean = mean(score for _, score in after)
        if before_mean - after_mean < self.drop:
            return None
        old, new = calls[i - 1][1], calls[i][1]
        return SignalFinding(
            message=f"score fell from {before_mean:.2f} to {after_mean:.2f} "
            f"after switching {old} to {new}",
            why=f"LLM calls on {new} average {after_mean:.2f}, {before_mean - after_mean:.2f} "
            f"below the earlier calls on {old}; the threshold is {self.drop}.",
            spans=[(span, f"{new} scored {score:.2f}") for span, score in after],
            measurements={
                "before_model": old,
                "after_model": new,
                "before_mean": round(before_mean, 3),
                "after_mean": round(after_mean, 3),
            },
        )


class LeakSignal(Signal):
    name = "leak"
    severity = "high"
    description = "Outputs contain an API key, an email address or a card number."
    remedy = "Strip sensitive data from the outputs"

    async def evaluate(self, spans):
        leaks = []
        for span in spans:
            kinds = sorted({kind for kind, _ in sensitive_matches(evidence.read_output(span))})
            leaks += [(span, f"{kind} in output") for kind in kinds]
        if leaks:
            return SignalFinding(
                message="sensitive data in outputs: "
                + ", ".join(
                    f"{span.name} ({reason.removesuffix(' in output')})" for span, reason in leaks
                ),
                why="These outputs match an API key, email address or valid card number "
                "pattern. Evidence redacts the matched values.",
                spans=leaks,
                measurements={"leaks": dict(Counter(reason for _, reason in leaks))},
            )
        return None


class RefusalSignal(Signal):
    name = "refusal"
    description = "Outputs refuse the task."
    remedy = "Fix the prompt or tools behind the refusals"
    pattern = re.compile(
        r"\b(?:i can(?:'|no)?t (?:help|assist)|i(?:'m| am) (?:not able|unable) to"
        r"|i won'?t be able to)\b",
        re.IGNORECASE,
    )

    async def evaluate(self, spans):
        refused = [
            (span, f'matched "{match.group()}"')
            for span in spans
            if (match := self.pattern.search(evidence.read_output(span)))
        ]
        if refused:
            return SignalFinding(
                message="outputs refusing the task: " + ", ".join(s.name for s, _ in refused),
                why="These outputs contain a refusal phrase.",
                spans=refused,
                measurements={"refusals": len(refused)},
            )
        return None


class BrokenJsonSignal(Signal):
    name = "broken_json"
    description = "Outputs open as JSON but do not parse."
    remedy = "Use structured output or validate the JSON before returning it"

    async def evaluate(self, spans):
        broken = []
        for span in spans:
            output = evidence.read_output(span)
            if output.startswith(("{", "[")):
                try:
                    json.loads(output)
                except json.JSONDecodeError as exc:
                    broken.append((span, f"{exc.msg} at character {exc.pos}"))
        if broken:
            return SignalFinding(
                message="outputs with malformed JSON: " + ", ".join(s.name for s, _ in broken),
                why="These outputs start with { or [ but fail to parse as JSON.",
                spans=broken,
                measurements={"broken": len(broken)},
            )
        return None


class HallucinatedToolSignal(Signal):
    name = "hallucinated_tool"
    description = "An LLM requests a tool that never runs in the trace."
    remedy = "Align the declared tools with the tools the agent runs"

    async def evaluate(self, spans):
        ran = {evidence.tool_name(span) for span in spans if evidence.is_tool(span)}
        requests = [
            (span, sorted(set(names) - ran - {""}))
            for span in spans
            if evidence.is_llm(span)
            and (names := evidence.requested_tools(evidence.read_output(span)))
        ]
        requests = [(span, unknown) for span, unknown in requests if unknown]
        if not requests:
            return None
        unknown = sorted({name for _, names in requests for name in names})
        return SignalFinding(
            message="LLM requested tools that never ran: " + ", ".join(unknown),
            why="These LLM calls requested tools with no matching tool span in the group.",
            spans=[(span, "requested " + ", ".join(names)) for span, names in requests],
            measurements={"tools": unknown},
        )


class SignalFactory:
    signals: ClassVar[dict[str, type[Signal]]] = {
        cls.name: cls
        for cls in (
            LowScoresSignal,
            ScoreDropSignal,
            RepeatedFailureSignal,
            HiddenFailureSignal,
            UnscorableSignal,
            ErrorsSignal,
            ToolFailureSignal,
            RetryStormSignal,
            SwallowedErrorSignal,
            AbandonedSignal,
            LoopSignal,
            RunawayTraceSignal,
            PlanDriftSignal,
            AbandonedSessionSignal,
            RepeatedInputSignal,
            FrustrationSignal,
            ContextOverflowSignal,
            SlowLLMSignal,
            TokenSpikeSignal,
            CostSpikeSignal,
            ModelChangeSignal,
            LeakSignal,
            RefusalSignal,
            BrokenJsonSignal,
            HallucinatedToolSignal,
        )
    }

    def get_signal(self, subscription: SignalSubscription) -> Signal:
        if subscription.signal_type not in self.signals:
            raise ValueError(f"Unknown signal type: {subscription.signal_type}")
        return self.signals[subscription.signal_type](subscription.project_id, subscription.config)

    def validate_config(self, signal_type: str, config: dict) -> dict:
        """Each override must name a param and keep its default's type."""
        if signal_type not in self.signals:
            raise ValueError(f"Unknown signal type: {signal_type}")
        cls = self.signals[signal_type]
        for key, value in config.items():
            if key not in cls.params:
                raise ValueError(f"'{signal_type}' has no parameter '{key}'.")
            default = getattr(cls, key)
            numeric = isinstance(default, int | float) and not isinstance(default, bool)
            if numeric and (isinstance(value, bool) or not isinstance(value, int | float)):
                raise ValueError(f"'{key}' must be a number.")
            if isinstance(default, int) and not isinstance(value, int):
                raise ValueError(f"'{key}' must be a whole number.")
            if not numeric and not isinstance(value, type(default)):
                raise ValueError(f"'{key}' must be a {type(default).__name__}.")
            if numeric and value <= 0:
                raise ValueError(f"'{key}' must be positive.")
        return config

    def catalog(self) -> list[dict]:
        return [
            {
                "signal_type": name,
                "description": cls.description,
                "default_enabled": cls.default_enabled,
                "params": {param: getattr(cls, param) for param in cls.params},
            }
            for name, cls in self.signals.items()
        ]


signal_factory = SignalFactory()


def effective_subscriptions(project_id, configured: list[SignalSubscription]):
    overridden = {s.signal_type for s in configured if s.capability_id is None}
    return configured + [
        SignalSubscription(id=None, project_id=project_id, signal_type=name)
        for name, cls in signal_factory.signals.items()
        if cls.default_enabled and name not in overridden
    ]


def group_key(span: Span) -> str:
    return (
        f"conversation:{span.conversation_id}" if span.conversation_id else f"trace:{span.trace_id}"
    )


def dedupe_key(subscription: SignalSubscription, key: str, day: str) -> str:
    identity = subscription.pk or f"default:{subscription.project_id}:{subscription.signal_type}"
    raw = f"{identity}:{key}:{day}"
    return hashlib.sha256(raw.encode()).hexdigest()[:64]


class SignalRunner:
    """Runs defaults and explicit overrides over one project's last day of spans."""

    def __init__(self, project_id):
        self.project_id = project_id

    async def batches(self):
        recent = (
            Span.objects.filter(
                project_id=self.project_id, received_at__gte=timezone.now() - WINDOW
            )
            .order_by("-start_time_ns", "span_id")
            .annotate(signal_bytes=span_payload_bytes())
            .values_list("span_id", "conversation_id", "trace_id", "signal_bytes")[
                :PROJECT_SPAN_CAP
            ]
        )
        groups = defaultdict(list)
        async for span_id, conversation_id, trace_id, size in recent:
            key = f"conversation:{conversation_id}" if conversation_id else f"trace:{trace_id}"
            groups[key].append((span_id, size))
        ids, size, oversized = {}, 0, 0
        for key, group in groups.items():
            group_size = sum(item[1] for item in group)
            if group_size > SIGNAL_BATCH_BYTES:
                oversized += 1
                continue
            if ids and (
                size + group_size > SIGNAL_BATCH_BYTES or len(ids) + len(group) > SIGNAL_BATCH_ROWS
            ):
                yield ids
                ids, size = {}, 0
            ids.update((item[0], key) for item in group)
            size += group_size
        if ids:
            yield ids
        if oversized:
            raise SignalInputTooLargeError(
                f"{oversized} signal groups exceed the {SIGNAL_BATCH_BYTES}-byte input limit; "
                "those groups were not checked or truncated"
            )

    async def run(self) -> int:
        configured = [
            s async for s in SignalSubscription.objects.filter(project_id=self.project_id)
        ]
        subscriptions = effective_subscriptions(self.project_id, configured)
        day = timezone.now().date().isoformat()
        count = 0
        rejected = 0
        errors = []
        try:
            async for ids in self.batches():
                try:
                    count += await self.run_batch(ids, configured, subscriptions, day)
                except SignalInputTooLargeError:
                    rejected += len(set(ids.values()))
        except SignalInputTooLargeError as exc:
            errors.append(str(exc))
        if rejected:
            errors.append(f"{rejected} signal groups changed or exceeded the batch byte limit")
        if errors:
            raise SignalInputTooLargeError("; ".join(errors))
        return count

    async def run_batch(self, ids, configured, subscriptions, day):
        # Recheck the complete batch in SQL so concurrent payload growth cannot bypass admission.
        selected = (
            Span.objects.filter(project_id=self.project_id, span_id__in=ids)
            .annotate(signal_total_bytes=Window(Sum(span_payload_bytes())))
            .filter(signal_total_bytes__lte=SIGNAL_BATCH_BYTES)
            .order_by("start_time_ns", "span_id")
        )
        spans = [span async for span in selected.aiterator(chunk_size=16)]
        if len(spans) != len(ids) or any(group_key(span) != ids[span.pk] for span in spans):
            raise SignalInputTooLargeError("Signal inputs changed or exceeded the batch byte limit")
        events = []
        for subscription in subscriptions:
            if not subscription.enabled:
                continue
            overridden_capabilities = {
                s.capability_id
                for s in configured
                if s.signal_type == subscription.signal_type and s.capability_id
            }
            scoped = [
                span
                for span in spans
                if (
                    span.capability_id == subscription.capability_id
                    if subscription.capability_id
                    else span.capability_id not in overridden_capabilities
                )
            ]
            signal = signal_factory.get_signal(subscription)
            for key, group in group_by(scoped, group_key).items():
                try:
                    finding = await signal.evaluate(group)
                    if not finding:
                        continue
                    last = finding.spans[-1][0] if finding.spans else group[-1]
                    capability_id = subscription.capability_id or last.capability_id
                    details = signal.details(finding, group, key, capability_id)
                except Exception as exc:  # noqa: BLE001 — one signal never blocks the others
                    logger.warning("Signal %s failed on %s: %s", signal.name, key, exc)
                    continue
                events.append(
                    SignalEvent(
                        project_id=self.project_id,
                        capability_id=capability_id,
                        subscription=subscription if subscription.pk else None,
                        signal_type=signal.name,
                        conversation_id=group[0].conversation_id,
                        trace_id=last.trace_id,
                        message=finding.message,
                        details=details,
                        dedupe_key=dedupe_key(subscription, key, day),
                    )
                )
        created = await SignalEvent.objects.abulk_create(events, ignore_conflicts=True)
        return len(created)


def projects_with_recent_spans() -> list:
    return list(
        Span.objects.filter(received_at__gte=timezone.now() - WINDOW)
        .order_by()
        .values_list("project_id", flat=True)
        .distinct()
    )
