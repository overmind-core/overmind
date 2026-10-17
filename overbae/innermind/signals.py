"""Automatic checks over recent conversations and traces, with per-project overrides."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from statistics import mean, median
from typing import ClassVar

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
    scores_of,
)
from overbae.innermind.span_processor import MAX_ATTEMPTS
from overbae.models import SignalEvent, SignalSubscription, Span

logger = logging.getLogger(__name__)

WINDOW = timedelta(days=1)
PROJECT_SPAN_CAP = 20_000
SIGNAL_BATCH_BYTES = 8 * 1024 * 1024
SIGNAL_BATCH_ROWS = 256


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


def spikes(spans: list[Span], factor: float, measure) -> list[str]:
    """LLM calls whose measure exceeds ``factor`` times the median of the calls measured."""
    values = [(span, value) for span in spans if evidence.is_llm(span) and (value := measure(span))]
    if len(values) < 3:
        return []
    typical = median(value for _, value in values)
    return [span.name for span, value in values if typical and value > factor * typical]


class Signal:
    name: ClassVar[str]
    description: ClassVar[str]
    default_enabled: ClassVar[bool] = True
    # Class attributes a subscription's ``config`` may override.
    params: ClassVar[tuple[str, ...]] = ()

    def __init__(self, project_id: str, config: dict | None = None):
        self.project_id = str(project_id)
        for key, value in (config or {}).items():
            if key in self.params:
                setattr(self, key, value)

    async def check(self, spans: list[Span]) -> str | None: ...


class LowScoresSignal(Signal):
    name = "low_scores"
    description = "The last scored spans all fall below the failing score."
    params = ("run",)
    run = 5

    async def check(self, spans):
        recent = scores_of(spans)[-self.run :]
        if len(recent) == self.run and all(score < FAIL_BELOW for score in recent):
            scores = ", ".join(f"{score:.2f}" for score in recent)
            return f"last {self.run} scored spans below {FAIL_BELOW}: {scores}"
        return None


class ScoreDropSignal(Signal):
    name = "score_drop"
    description = "The mean of the most recent scores falls against the earlier mean."
    params = ("window", "drop")
    window = 5
    drop = 0.2

    async def check(self, spans):
        scores = scores_of(spans)
        if len(scores) < 2 * self.window:
            return None
        before, recent = mean(scores[: -self.window]), mean(scores[-self.window :])
        if before - recent >= self.drop:
            return f"mean score fell from {before:.2f} to {recent:.2f} among spans {spans}"
        return None


class RepeatedFailureSignal(Signal):
    name = "repeated_failure"
    description = "One expectation fails repeatedly."
    params = ("times",)
    times = 3

    async def check(self, spans):
        failures = Counter(
            metric.get("expectation", name)
            for span in spans
            for name, metric in span.eval_attributes.get("metrics", {}).items()
            if metric.get("verdict") == "no"
        )
        repeated = [f'"{text}" ({n}x)' for text, n in failures.items() if n >= self.times]
        if repeated:
            return "expectations failing repeatedly: " + ", ".join(repeated)
        return None


class HiddenFailureSignal(Signal):
    name = "hidden_failure"
    description = "A step fails under a root answer that passes."

    async def check(self, spans):
        hidden = []
        for trace in group_by(spans, lambda span: span.trace_id).values():
            for root in roots_of(trace):
                if (score_of(root) or 0) >= REASON_BELOW:
                    hidden += [span.name for span in trace if not span.is_root and is_failing(span)]
        if hidden:
            return "failing steps under passing answers: " + ", ".join(hidden)
        return None


class UnscorableSignal(Signal):
    name = "unscorable"
    description = "Evaluated spans end without a score."
    params = ("rate",)
    rate = 0.5

    async def check(self, spans):
        evaluated = [span for span in spans if "score" in span.eval_attributes]
        unscored = [span for span in evaluated if score_of(span) is None]
        if evaluated and len(unscored) / len(evaluated) >= self.rate:
            return f"{len(unscored)} of {len(evaluated)} evaluated spans have no score"
        return None


class ErrorsSignal(Signal):
    name = "errors"
    description = "Spans end with an error status."

    async def check(self, spans):
        failed = [span.name for span in spans if evidence.status(span) == "error"]
        if failed:
            return f"{len(failed)} of {len(spans)} spans errored: " + ", ".join(failed)
        return None


class ToolFailureSignal(Signal):
    name = "tool_failure"
    description = "A tool errors on a large share of its calls."
    params = ("min_calls", "rate")
    min_calls = 3
    rate = 0.5

    async def check(self, spans):
        calls = defaultdict(list)
        for span in spans:
            if evidence.is_tool(span):
                calls[evidence.tool_name(span)].append(span)
        failing = []
        for name, group in calls.items():
            errored = sum(evidence.status(span) == "error" for span in group)
            if len(group) >= self.min_calls and errored / len(group) >= self.rate:
                failing.append(f"{name} ({errored}/{len(group)})")
        if failing:
            return "tools failing: " + ", ".join(failing)
        return None


class RetryStormSignal(Signal):
    name = "retry_storm"
    description = "One call is retried repeatedly under the same parent after an error."
    params = ("times",)
    times = 3

    async def check(self, spans):
        calls = defaultdict(list)
        for span in spans:
            if not span.is_root:
                calls[(span.parent_span_id, span.name)].append(span)
        storms = [
            f"{name} ({len(group)}x)"
            for (_, name), group in calls.items()
            if len(group) >= self.times and any(evidence.status(s) == "error" for s in group)
        ]
        if storms:
            return "calls retried under one parent: " + ", ".join(storms)
        return None


class SwallowedErrorSignal(Signal):
    name = "swallowed_error"
    description = "A span errors inside a trace whose root finishes ok."

    async def check(self, spans):
        swallowed = []
        for trace in group_by(spans, lambda span: span.trace_id).values():
            if any(evidence.status(root) == "ok" for root in roots_of(trace)):
                swallowed += [span.name for span in trace if evidence.status(span) == "error"]
        if swallowed:
            return "errors inside traces that finished ok: " + ", ".join(swallowed)
        return None


class AbandonedSignal(Signal):
    name = "abandoned"
    description = "A trace has no root span long after its first span arrived."
    params = ("minutes",)
    minutes = 10

    async def check(self, spans):
        cutoff = timezone.now() - timedelta(minutes=self.minutes)
        stuck = [
            trace_id
            for trace_id, trace in group_by(spans, lambda span: span.trace_id).items()
            if not roots_of(trace) and min(span.received_at for span in trace) < cutoff
        ]
        if stuck:
            return f"traces without a root after {self.minutes} minutes: " + ", ".join(stuck)
        return None


class LoopSignal(Signal):
    name = "loop"
    description = "A tool is called repeatedly with the same input in one trace."
    params = ("times",)
    times = 3

    async def check(self, spans):
        calls = Counter(
            (span.trace_id, evidence.tool_name(span), evidence.read_input(span))
            for span in spans
            if evidence.is_tool(span)
        )
        loops = [f"{name} ({n}x)" for (_, name, _), n in calls.items() if n >= self.times]
        if loops:
            return "tools called repeatedly with the same input: " + ", ".join(loops)
        return None


class RunawayTraceSignal(Signal):
    name = "runaway_trace"
    description = "A trace has many times the usual number of spans."
    params = ("factor",)
    factor = 3

    async def check(self, spans):
        sizes = {
            trace_id: len(trace)
            for trace_id, trace in group_by(spans, lambda span: span.trace_id).items()
        }
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
    """Each scored step is judged once and its verdict stored as ``eval_attributes.drift``;
    a judge that keeps failing stops after ``MAX_ATTEMPTS``."""

    name = "plan_drift"
    default_enabled = False
    description = "Jev judges steps as not working toward the intent."
    expectation = "The step works toward the intent."

    async def check(self, spans):
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
            span.name
            for span in steps
            if (score := span.eval_attributes.get("drift")) is not None and score < FAIL_BELOW
        ]
        if drifting:
            return "steps drifting from the intent: " + ", ".join(drifting)
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
    minutes = 30

    async def check(self, spans):
        if not spans or not spans[-1].conversation_id:
            return None
        last = spans[-1]
        idle = started_at(last) < datetime.now(UTC) - timedelta(minutes=self.minutes)
        if idle and (evidence.status(last) == "error" or is_failing(last)):
            return f"conversation ended on a failure: {last.name}"
        return None


class RepeatedInputSignal(Signal):
    name = "repeated_input"
    description = "The user repeats their previous message."

    async def check(self, spans):
        inputs = [" ".join(evidence.read_input(root).lower().split()) for root in roots_of(spans)]
        if any(a and a == b for a, b in zip(inputs, inputs[1:], strict=False)):
            return "the user repeated their last message"
        return None


class FrustrationSignal(Signal):
    name = "frustration"
    description = "The user corrects the agent."
    pattern = re.compile(
        r"\b(?:that'?s (?:wrong|not right)|not what i (?:asked|meant|wanted)|no,? i meant"
        r"|you misunderstood|try again|still (?:wrong|broken|not working)|doesn'?t work)\b",
        re.IGNORECASE,
    )

    async def check(self, spans):
        if any(self.pattern.search(evidence.read_input(root)) for root in roots_of(spans)):
            return "the user corrected the agent"
        return None


class ContextOverflowSignal(Signal):
    name = "context_overflow"
    description = "Root scores fall in the second half of a long conversation."
    params = ("turns", "drop")
    turns = 10
    drop = 0.2

    async def check(self, spans):
        scores = scores_of(roots_of(spans))
        if len(scores) < self.turns:
            return None
        half = len(scores) // 2
        before, after = mean(scores[:half]), mean(scores[half:])
        if before - after >= self.drop:
            return f"root scores fell from {before:.2f} to {after:.2f} over {len(scores)} turns"
        return None


class SlowLLMSignal(Signal):
    name = "slow_llm"
    description = "LLM calls take many times the usual latency."
    params = ("factor",)
    factor = 3

    async def check(self, spans):
        slow = spikes(spans, self.factor, evidence.seconds)
        if slow:
            return f"LLM calls over {self.factor}x the usual latency: " + ", ".join(slow)
        return None


class TokenSpikeSignal(Signal):
    name = "token_spike"
    description = "LLM calls use many times the usual tokens."
    params = ("factor",)
    factor = 3

    async def check(self, spans):
        heavy = spikes(spans, self.factor, evidence.tokens)
        if heavy:
            return f"LLM calls over {self.factor}x the usual tokens: " + ", ".join(heavy)
        return None


class CostSpikeSignal(Signal):
    name = "cost_spike"
    description = "LLM calls cost many times the usual amount."
    params = ("factor",)
    factor = 3

    async def check(self, spans):
        costly = spikes(spans, self.factor, evidence.cost)
        if costly:
            return f"LLM calls over {self.factor}x the usual cost: " + ", ".join(costly)
        return None


class ModelChangeSignal(Signal):
    name = "model_change"
    description = "Scores fall after the model changes."
    params = ("drop",)
    drop = 0.2

    async def check(self, spans):
        calls = [
            (span, model)
            for span in spans
            if evidence.is_llm(span) and (model := evidence.model_name(span))
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
    name = "leak"
    description = "Outputs contain an API key, an email address or a card number."
    patterns: ClassVar[dict[str, re.Pattern]] = {
        "api key": re.compile(
            r"\b(?:sk-[A-Za-z0-9_-]{20,}|phx_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{36})\b"
        ),
        "email": re.compile(r"\b[\w.+-]+@[\w-]+\.[A-Za-z]{2,}\b"),
        "card number": re.compile(r"\b(?:\d[ -]?){13,15}\d\b"),
    }

    @staticmethod
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

    async def check(self, spans):
        leaks = [
            f"{span.name} ({kind})"
            for span in spans
            for kind, pattern in self.patterns.items()
            if any(
                kind != "card number" or self.valid_card_number(match.group())
                for match in pattern.finditer(evidence.read_output(span))
            )
        ]
        if leaks:
            return "sensitive data in outputs: " + ", ".join(leaks)
        return None


class RefusalSignal(Signal):
    name = "refusal"
    description = "Outputs refuse the task."
    pattern = re.compile(
        r"\b(?:i can(?:'|no)?t (?:help|assist)|i(?:'m| am) (?:not able|unable) to"
        r"|i won'?t be able to)\b",
        re.IGNORECASE,
    )

    async def check(self, spans):
        refused = [s.name for s in spans if self.pattern.search(evidence.read_output(s))]
        if refused:
            return "outputs refusing the task: " + ", ".join(refused)
        return None


class BrokenJsonSignal(Signal):
    name = "broken_json"
    description = "Outputs open as JSON but do not parse."

    async def check(self, spans):
        broken = []
        for span in spans:
            output = evidence.read_output(span)
            if output.startswith(("{", "[")):
                try:
                    json.loads(output)
                except json.JSONDecodeError:
                    broken.append(span.name)
        if broken:
            return "outputs with malformed JSON: " + ", ".join(broken)
        return None


class HallucinatedToolSignal(Signal):
    name = "hallucinated_tool"
    description = "An LLM requests a tool that never runs in the trace."

    async def check(self, spans):
        ran = {evidence.tool_name(span) for span in spans if evidence.is_tool(span)}
        requested = {
            name
            for span in spans
            if evidence.is_llm(span)
            for name in evidence.requested_tools(evidence.read_output(span))
        }
        unknown = sorted(name for name in requested - ran if name)
        if unknown:
            return "LLM requested tools that never ran: " + ", ".join(unknown)
        return None


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
                    message = await signal.check(group)
                except Exception as exc:  # noqa: BLE001 — one signal never blocks the others
                    logger.warning("Signal %s failed on %s: %s", signal.name, key, exc)
                    continue
                if not message:
                    continue
                first = group[0]
                events.append(
                    SignalEvent(
                        project_id=self.project_id,
                        capability_id=subscription.capability_id or first.capability_id,
                        subscription=subscription if subscription.pk else None,
                        signal_type=signal.name,
                        conversation_id=first.conversation_id,
                        trace_id=group[-1].trace_id,
                        message=message,
                        details={
                            "spans": len(group),
                            "group": key,
                            "trace_ids": list(dict.fromkeys(span.trace_id for span in group)),
                        },
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
