# Score PostHog trace spans with Jev and show where a trajectory goes wrong.
#   uv run python trace_evals.py               # every trace from the last 24h
#   uv run python trace_evals.py <trace_id>    # one trace
# Needs POSTHOG_PERSONAL_API_KEY (phx_..., query:read scope) and OPENROUTER_API_KEY.
# TRACE_SERVICE limits the query to one service; unset reads every service.
#
# Each item in a span's expectations is one metric, judged against the span's
# intent, input and output. Attribute names, in order of preference:
#   expectations  overmind.expectations, expectations, else one expect string
#   input         input, overmind.input, else the overmind.arg.* arguments
#   output        output, overmind.output
#   intent        intent, overmind.intent, else the parent's; a root uses its input
# Metrics under REASON_BELOW also get a written reason from a generative model.

import json
import os
import re
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean

import httpx
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table

load_dotenv(Path(__file__).resolve().parent / ".env", override=True)

POSTHOG_HOST = os.environ.get("POSTHOG_HOST", "https://eu.posthog.com")
POSTHOG_PROJECT = os.environ.get("POSTHOG_PROJECT_ID", "@current")
SERVICE = os.environ.get("TRACE_SERVICE", "").strip()
OPENROUTER = "https://openrouter.ai/api/v1"
JEV_MODEL = "typesafe/jev-1.13"
REASON_MODEL = "openai/gpt-5.6-luna"

ARG_PREFIX = "overmind.arg."
VALUE = {"yes": 1.0, "partial": 0.5, "no": 0.0}
FAIL_BELOW = 0.5
REASON_BELOW = 0.75
# Jev rejects a request whose state + question exceeds ~31k UTF-8 bytes.
JEV_STATE_LIMIT = 30_000

MAX_CONCURRENT_REQUESTS = 128
# Shared by every OpenRouter call, Jev and reasons alike.
openrouter_slots = threading.BoundedSemaphore(MAX_CONCURRENT_REQUESTS)

QUESTION = {
    "type": "choice",
    "instructions": "Given the intent and the input, does the output meet the expectation?",
    "criteria": {
        "yes": "The output fully meets the expectation.",
        "partial": "The output meets part of the expectation, or meets it with errors.",
        "no": "The output does not meet the expectation.",
    },
}


@dataclass
class Metric:
    expectation: str
    score: float | None = None
    verdict: str = ""
    error: str = ""


@dataclass
class Span:
    span_id: str
    parent_id: str | None
    name: str
    start: str
    attributes: dict
    children: list["Span"] = field(default_factory=list)
    intent: str = ""
    input: str = ""
    output: str = ""
    expectations: list[str] = field(default_factory=list)
    metrics: list[Metric] = field(default_factory=list)
    skipped: str = ""

    @property
    def scores(self) -> list[float]:
        return [m.score for m in self.metrics if m.score is not None]

    @property
    def score(self) -> float | None:
        return mean(self.scores) if self.scores else None

    @property
    def failing(self) -> bool:
        return any(score < FAIL_BELOW for score in self.scores)


def fetch_traces(trace_id: str | None, hours: int = 24) -> dict[str, list[Span]]:
    where = f"timestamp >= now() - INTERVAL {hours} HOUR"
    if SERVICE:
        where += " AND service_name = '" + SERVICE.replace("'", "''") + "'"
    if trace_id:
        where += f" AND lower(hex(tryBase64Decode(trace_id))) = '{trace_id.lower()}'"
    query = f"""
        SELECT lower(hex(tryBase64Decode(trace_id))),
               lower(hex(tryBase64Decode(span_id))),
               lower(hex(tryBase64Decode(parent_span_id))),
               name, timestamp, attributes
        FROM posthog.trace_spans
        WHERE {where}
        ORDER BY timestamp
        LIMIT 5000
    """
    resp = httpx.post(
        f"{POSTHOG_HOST}/api/projects/{POSTHOG_PROJECT}/query/",
        headers={"Authorization": f"Bearer {os.environ['POSTHOG_PERSONAL_API_KEY']}"},
        json={"query": {"kind": "HogQLQuery", "query": query}},
        timeout=60,
    )
    resp.raise_for_status()

    traces: dict[str, list[Span]] = {}
    for tid, span_id, parent_id, name, start, attributes in resp.json()["results"]:
        if isinstance(attributes, str):
            attributes = json.loads(attributes)
        span = Span(
            span_id=span_id,
            # A root span's parent decodes to all zeros.
            parent_id=parent_id if parent_id and parent_id.strip("0") else None,
            name=name,
            start=str(start),
            attributes=attributes or {},
        )
        traces.setdefault(tid, []).append(span)
    return traces


def build_tree(spans: list[Span]) -> list[Span]:
    by_id = {span.span_id: span for span in spans}
    roots = []
    for span in sorted(spans, key=lambda s: s.start):
        parent = by_id.get(span.parent_id)
        if parent is None:
            roots.append(span)
        else:
            parent.children.append(span)
    return roots


def walk(spans: list[Span], depth: int = 0):
    for span in spans:
        yield span, depth
        yield from walk(span.children, depth + 1)


def first_text(attributes: dict, *keys: str) -> str:
    for key in keys:
        value = attributes.get(key)
        if isinstance(value, (str, int, float)) and str(value).strip():
            return str(value).strip()
    return ""


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


def read_expectations(attributes: dict) -> list[str]:
    value = attributes.get("overmind.expectations", attributes.get("expectations"))
    # PostHog returns an OTLP array attribute as a JSON-encoded string.
    if isinstance(value, str) and value.lstrip().startswith("["):
        with suppress(json.JSONDecodeError):
            value = json.loads(value)
    if isinstance(value, str):
        value = [value]
    if isinstance(value, list):
        return [item.strip() for item in value if isinstance(item, str) and item.strip()]
    expect = first_text(attributes, "expect", "overmind.expect")
    return [expect] if expect else []


def read_contracts(roots: list[Span]) -> None:
    def visit(span: Span, parent_intent: str | None) -> None:
        attributes = span.attributes
        span.input = read_input(attributes)
        span.output = first_text(attributes, "output", "overmind.output")
        span.expectations = read_expectations(attributes)
        span.intent = first_text(attributes, "intent", "overmind.intent") or (
            span.input if parent_intent is None else parent_intent
        )
        for child in span.children:
            visit(child, span.intent)

    for root in roots:
        visit(root, None)


def evidence(span: Span, expectation: str) -> dict:
    return {
        "intent": span.intent,
        "expectation": expectation,
        "input": span.input,
        "output": span.output,
    }


def judge(span: Span, expectation: str) -> Metric:
    state = evidence(span, expectation)
    if len(json.dumps(state).encode()) > JEV_STATE_LIMIT:
        return Metric(expectation, error="too large for Jev")
    try:
        with openrouter_slots:
            resp = httpx.post(
                f"{OPENROUTER}/systemone",
                headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                json={"model": JEV_MODEL, "state": state, "questions": {"meets": QUESTION}},
                timeout=30,
            )
    except httpx.HTTPError as exc:
        return Metric(expectation, error=f"Jev request failed: {type(exc).__name__}")
    if not resp.is_success:
        return Metric(expectation, error=f"Jev HTTP {resp.status_code}")
    answer = resp.json()["answers"]["meets"]
    score = sum(VALUE[choice] * p for choice, p in answer["probabilities"].items())
    return Metric(expectation, score=score, verdict=answer["choice"])


def score_spans(spans: list[Span]) -> None:
    jobs = []
    for span in spans:
        missing = [
            name
            for name in ("intent", "input", "output", "expectations")
            if not getattr(span, name)
        ]
        if missing:
            span.skipped = "missing " + ", ".join(missing)
        else:
            jobs += [(span, expectation) for expectation in span.expectations]

    with ThreadPoolExecutor(MAX_CONCURRENT_REQUESTS) as pool:
        metrics = pool.map(lambda job: judge(*job), jobs)
        for (span, _), metric in zip(jobs, metrics, strict=True):
            span.metrics.append(metric)


def explain(span: Span, metric: Metric) -> str:
    prompt = (
        f"An evaluator scored this step {metric.score:.2f} out of 1 against its expectation.\n"
        "In one or two sentences, state the specific reason the output falls short. "
        "Name what is missing or wrong; do not restate the expectation.\n\n"
        + json.dumps(evidence(span, metric.expectation), ensure_ascii=False, indent=1)
    )
    try:
        with openrouter_slots:
            resp = httpx.post(
                f"{OPENROUTER}/chat/completions",
                headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}"},
                json={"model": REASON_MODEL, "messages": [{"role": "user", "content": prompt}]},
                timeout=90,
            )
        resp.raise_for_status()
        text = resp.json()["choices"][0]["message"]["content"] or ""
        return " ".join(text.split()) or "(empty reason)"
    except Exception as exc:
        return f"(reason unavailable: {exc})"


def fmt(score: float | None) -> str:
    return "  -  " if score is None else f"{score:.2f}"


def report_trace(trace_id: str, roots: list[Span], trajectory: float | None) -> None:
    outcome = next((root.score for root in roots if root.score is not None), None)
    print(f"\ntrace {trace_id}")
    print(f"  trajectory {fmt(trajectory)}   outcome {fmt(outcome)}")

    for span, depth in walk(roots):
        indent = "  " * (depth + 1)
        if span.skipped:
            status = f"  -   skipped: {span.skipped}"
        elif span.score is None:
            status = f"  -   error: {span.metrics[0].error}"
        else:
            status = f"{fmt(span.score)} {'FAIL' if span.failing else 'ok'}"
        print(f"{indent}{span.name:<{max(8, 32 - len(indent))}} {status}")
        for metric in span.metrics:
            result = f"error: {metric.error}" if metric.error else f"{metric.verdict:<7}"
            print(f"{indent}    {fmt(metric.score)} {result} {metric.expectation}")

    failing = [span for span, _ in walk(roots) if span.failing]
    if not failing:
        print("  no failing nodes")
        return
    print("  failing nodes:")
    for span in failing:
        below = sorted({child.name for child, _ in walk(span.children) if child.failing})
        if below:
            why = f"also failing under it: {', '.join(below)}"
        else:
            why = "fails on its own; everything under it passes"
        print(f"    {span.name} ({fmt(span.score)}) - {why}")
        for metric in span.metrics:
            if metric.score is not None and metric.score < FAIL_BELOW:
                print(f"      {fmt(metric.score)} {metric.expectation}")


def report_reasons(low: list[tuple[str, Span, Metric]]) -> None:
    print(f"\n{'=' * 30} Generating why they are failing {'=' * 30}")
    print(f"{len(low)} metrics scored under {REASON_BELOW}; asking {REASON_MODEL}...\n")
    with ThreadPoolExecutor(MAX_CONCURRENT_REQUESTS) as pool:
        reasons = list(pool.map(lambda row: explain(row[1], row[2]), low))

    table = Table(show_lines=True)
    table.add_column("#", justify="right")
    table.add_column("Trace")
    table.add_column("Node")
    table.add_column("Score", justify="right")
    table.add_column("Expectation", ratio=1)
    table.add_column("Why it falls short", ratio=2)
    for i, ((trace_id, span, metric), reason) in enumerate(zip(low, reasons, strict=True), 1):
        score = fmt(metric.score)
        if metric.score < FAIL_BELOW:
            score = f"[red]{score}[/red]"
        table.add_row(str(i), trace_id[:8], span.name, score, metric.expectation, reason)
    Console().print(table)


def main() -> None:
    trace_id = sys.argv[1] if len(sys.argv) > 1 else None
    if trace_id and not re.fullmatch(r"[0-9a-fA-F]{32}", trace_id):
        sys.exit("trace id must be 32 hex characters")
    traces = fetch_traces(trace_id)
    if not traces:
        print("no traces found")
        return

    roots_by_trace = {tid: build_tree(spans) for tid, spans in traces.items()}
    for roots in roots_by_trace.values():
        read_contracts(roots)
    score_spans([span for spans in traces.values() for span in spans])

    trajectories = []
    low = []
    for tid, spans in traces.items():
        roots = roots_by_trace[tid]
        scores = [score for span in spans for score in span.scores]
        trajectory = mean(scores) if scores else None
        report_trace(tid, roots, trajectory)
        if trajectory is not None:
            trajectories.append(trajectory)
        low += [
            (tid, span, metric)
            for span, _ in walk(roots)
            for metric in span.metrics
            if metric.score is not None and metric.score < REASON_BELOW
        ]

    if trajectories:
        print(f"\n{len(traces)} traces, mean trajectory {mean(trajectories):.2f}")
    if low:
        report_reasons(low)


if __name__ == "__main__":
    main()
