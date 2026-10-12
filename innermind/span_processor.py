import asyncio
import inspect
import logging
import re
import time
from collections.abc import Callable
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from statistics import mean

from django.db.models import Count, Exists, F, IntegerField, OuterRef, Q, Subquery
from django.db.models.fields.json import KT
from django.db.models.functions import Cast
from opentelemetry.trace import Span as OTSpan

from innermind import deterministic
from innermind.evals import Evaluator, JevEvaluator, OvermindEvaluator
from innermind.evals_trajectory import score_trajectory
from innermind.innermind import DeterministicCheck, Span
from innermind.signals import SignalFactory, create_signal_from_spans
from innermind.utils import group_by, read_expectations, read_intent, roots_of

logger = logging.getLogger(__name__)


class SpanProcessor:
    def get_llm_evaluators(self) -> list[Evaluator]:
        raise NotImplementedError("Subclasses must implement this method")

    async def get_evaluators(self, span: Span, checks: list[DeterministicCheck]) -> list[Evaluator]:
        trace = await deterministic.read_trace(span) if checks else []
        return [*self.get_llm_evaluators(), *(OvermindEvaluator(check, trace) for check in checks)]

    async def evaluate(self, span: Span) -> Span:
        """Writes "score" only once the result is final; a span without it is retried."""
        intent = await read_intent(span)
        if intent is None:
            return span
        checks = await deterministic.checks_for(span)
        # Checks read children and siblings; the root ends last, so wait for it.
        if checks and not await deterministic.root_arrived(span.trace_id):
            return span

        span.eval_attributes = {
            "version": span.eval_attributes.get("version", 0) + 1,
            "intent": intent,
            "expectations": read_expectations(span.attributes),
        }
        evaluators = await self.get_evaluators(span, checks)
        results = await asyncio.gather(*(evaluator.evaluate(span) for evaluator in evaluators))
        scores = [result.score for result in results if result.score is not None]
        span.eval_attributes |= {
            "score": mean(scores) if scores else None,
            "evaluators": {result.name: asdict(result) for result in results},
        }
        return span

    def process(self, span: Span) -> Span:
        raise NotImplementedError("Subclasses must implement this method")


class LLMSpanProcessor(SpanProcessor):
    def get_llm_evaluators(self) -> list[Evaluator]:
        return [JevEvaluator()]

    def process(self, span: Span) -> Span:
        return super().process(span)


class FunctionSpanProcessor(SpanProcessor):
    def get_llm_evaluators(self) -> list[Evaluator]:
        return [JevEvaluator()]

    def process(self, span: Span) -> Span:
        return super().process(span)


class WorkflowSpanProcessor(SpanProcessor):
    def get_llm_evaluators(self) -> list[Evaluator]:
        return [JevEvaluator()]

    def process(self, span: Span) -> Span:
        return super().process(span)


class ToolSpanProcessor(SpanProcessor):
    def get_llm_evaluators(self) -> list[Evaluator]:
        return [JevEvaluator()]

    def process(self, span: Span) -> Span:
        return super().process(span)


class GenericSpanProcessor(SpanProcessor):
    def get_llm_evaluators(self) -> list[Evaluator]:
        return [JevEvaluator()]

    def process(self, span: Span) -> Span:
        return super().process(span)


class SpanProcessorFactory:
    def get_processor(self, span: Span) -> SpanProcessor:
        if span.kind == "span":
            return GenericSpanProcessor()
        elif span.kind == "llm":
            return LLMSpanProcessor()
        elif span.kind == "function":
            return FunctionSpanProcessor()
        elif span.kind == "workflow":
            return WorkflowSpanProcessor()
        elif span.kind == "tool":
            return ToolSpanProcessor()
        else:
            raise ValueError(f"Unknown span kind: {span.kind}")


factory = SpanProcessorFactory()


def ingest(raw_span: OTSpan):
    """handles injection of spans into the database"""
    # process the span into a format that can be used further down the line
    processor = factory.get_processor(raw_span)
    span = processor.process(raw_span)
    # save span to database
    return span


def tranform_span(span: Span) -> Span:
    """transforms a span into a format that can be used further down the line"""
    return span


async def score_span(span: Span) -> Span:
    """
    scores a span based on the expectations

    this doesnt save the score to the database, it just returns updated span with the score
    """
    processor = factory.get_processor(span)
    return await processor.evaluate(span)


def model_finetune(spans: list[Span], model: str, **kwargs) -> None:
    """finetunes the model based on the spans"""
    # get a dataset of spans
    # run evals on those spans aka baselines
    # finetune the model on the spans
    # run evals on the finetuned model
    # compare the scores of the finetuned model to the baselines
    # if the finetuned model is better, save the model
    # if the finetuned model is not better, don't save the model


def model_backtest(spans: list[Span], model: str, **kwargs) -> None:
    """backtests the model based on the spans"""
    # get a dataset of spans
    # run evals on those spans aka baselines
    # backtest multiple models on the spans by replaying the spans
    # compare the scores of the backtested model to the baselines
    # return the best model


def trajectory_optimizer(spans: list[Span], **kwargs) -> None:
    """optimizes the completed trajectory by rephrasing the spans to improve the trajectory"""
    # get a dataset of spans
    # run evals on those spans aka baselines
    # optimize the spans by updating system prompts, models, or updating the tool call descriptions, then replay the spans.
    # run evals on the new spans and compare the scores to the baselines
    # return the system prompts, models, and tool call descriptions with the best scores aka code diff with best scores compared to the baselines


def model_inference(model: str, **kwargs) -> None:
    """run inference on the model"""


SCHEDULE: list[tuple[Callable, int]] = []


def cron_decorator(cron_expression: str) -> Callable:
    """registers a job for run_forever; only "*/N * * * *" (every N minutes) is supported"""
    every = re.fullmatch(r"\*/(\d+) \* \* \* \*", cron_expression)
    if every is None:
        raise ValueError(f"Unsupported cron expression: {cron_expression}")

    def decorator(func: Callable) -> Callable:
        SCHEDULE.append((func, int(every.group(1)) * 60))
        return func

    return decorator


async def run_job(job: Callable, interval_seconds: int) -> None:
    while True:
        started = time.monotonic()
        try:
            result = job()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception("%s failed", job.__name__)
        await asyncio.sleep(max(0.0, interval_seconds - (time.monotonic() - started)))


async def run_forever() -> None:
    """
    runs every scheduled job in this one event loop: openrouter_client is bound to the loop
    it first runs in, so the jobs must never be driven by separate asyncio.run calls
    """
    await asyncio.gather(*(run_job(job, interval) for job, interval in SCHEDULE))


@cron_decorator("*/1 * * * *")
async def span_scoring_queue() -> None:
    """run span scoring queue every 60 seconds"""
    # find all spans that are not in use and have not been scored yet
    spans = [
        span
        async for span in Span.objects.filter(in_use=False, eval_attributes__score__isnull=True)
    ]

    semaphore = asyncio.Semaphore(128)

    async def process_span(span: Span):
        async with semaphore:
            # Mark span as in use in the DB to avoid reprocessing in concurrent jobs
            span.in_use = True
            await span.asave(update_fields=["in_use"])

        try:
            scored_span = await score_span(span)
        except Exception as exc:
            # still no "score", so the next run retries it
            span.eval_attributes["error"] = f"{type(exc).__name__}: {exc}"
            scored_span = span
        scored_span.in_use = False
        return scored_span

    tasks = [process_span(span) for span in spans]
    spans_after_scoring = []
    for coro in asyncio.as_completed(tasks):
        scored_span = await coro
        spans_after_scoring.append(scored_span)

    if spans_after_scoring:
        await Span.objects.abulk_update(spans_after_scoring, fields=["eval_attributes", "in_use"])


@cron_decorator("*/5 * * * *")
async def trajectory_scoring_queue() -> None:
    """run trajectory scoring queue every 5 minutes"""
    # a finished root whose spans are all evaluated, and whose trace grew since it was last scored
    in_trace = Span.objects.filter(trace_id=OuterRef("trace_id"))
    span_count = in_trace.values("trace_id").annotate(n=Count("pk")).values("n")[:1]
    roots = (
        Span.objects.filter(Q(parent_id__isnull=True) | Q(parent_id=""), in_use=False)
        .exclude(status="pending")
        .filter(~Exists(in_trace.filter(eval_attributes__score__isnull=True)))
        .annotate(
            span_count=Subquery(span_count),
            scored_count=Cast(KT("eval_attributes__trajectory__span_count"), IntegerField()),
        )
        .filter(Q(scored_count__isnull=True) | ~Q(scored_count=F("span_count")))
    )
    trace_ids = [trace_id async for trace_id in roots.values_list("trace_id", flat=True)]
    if not trace_ids:
        return

    spans = [span async for span in Span.objects.filter(trace_id__in=trace_ids)]
    traces = list(group_by(spans, "trace_id").values())
    await Span.objects.filter(pk__in=[roots_of(trace)[0].pk for trace in traces]).aupdate(
        in_use=True
    )

    async def process_trace(trace: list[Span]) -> Span:
        try:
            root = await score_trajectory(trace)
        except Exception as exc:
            # span_count is unchanged, so the next run retries it
            root = roots_of(trace)[0]
            root.eval_attributes["trajectory_error"] = f"{type(exc).__name__}: {exc}"
        root.in_use = False
        return root

    scored_roots = await asyncio.gather(*(process_trace(trace) for trace in traces))
    await Span.objects.abulk_update(scored_roots, fields=["eval_attributes", "in_use"])


SIGNAL_WINDOW = timedelta(days=1)
# plan_drift makes a Jev call per step on every run, so it is left to explicit calls.
SIGNALS_TO_RUN = [name for name in SignalFactory.signals if name != "plan_drift"]


@cron_decorator("*/5 * * * *")
async def signal_queue() -> None:
    """run every signal over each session's recent spans every 5 minutes"""
    since = (datetime.now(UTC) - SIGNAL_WINDOW).isoformat(timespec="microseconds")
    spans = [span async for span in Span.objects.filter(start__gte=since).exclude(session_id="")]
    for session_id, session in group_by(spans, "session_id").items():
        for signal_type in SIGNALS_TO_RUN:
            try:
                await create_signal_from_spans(session, signal_type, f"session {session_id}")
            except Exception as exc:
                print(f"signal {signal_type} for session {session_id} failed: {exc}")


@cron_decorator("*/5 * * * *")
def span_altering_queue() -> None:
    """run span altering queue every 5 minutes"""
    ...


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_forever())
