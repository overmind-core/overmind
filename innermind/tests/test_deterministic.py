"""E2E for innermind span evaluators: LLM judges and deterministic checks side by side.

Run:
    OPENROUTER_API_KEY=test uv run python -m innermind.tests.test_deterministic

Writes innermind/tests/.runs/deterministic.json with the command, fixtures, and
observed eval_attributes.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("OPENROUTER_API_KEY", "test-key-for-deterministic")

from asgiref.sync import sync_to_async  # noqa: E402

from innermind import checks  # noqa: E402
from innermind.deterministic import WALL_SECONDS, audit  # noqa: E402
from innermind.innermind import DeterministicCheck, Span, ensure_schema  # noqa: E402
from innermind.span_processor import score_span  # noqa: E402
from innermind.utils import Metric  # noqa: E402

RUNS = Path(__file__).resolve().parent / ".runs"
ARTIFACT = RUNS / "deterministic.json"
COMMAND = "OPENROUTER_API_KEY=test uv run python -m innermind.tests.test_deterministic"


def make_span(
    *,
    span_id: str,
    trace_id: str,
    parent_id: str | None = None,
    kind: str = "function",
    name: str = "step",
    attributes: dict | None = None,
) -> Span:
    return Span(
        span_id=span_id,
        parent_id=parent_id,
        trace_id=trace_id,
        kind=kind,
        name=name,
        start=datetime.now(UTC).isoformat(timespec="microseconds"),
        status="ok",
        attributes=attributes or {},
        eval_attributes={},
    )


async def reset(trace_id: str) -> None:
    await Span.objects.filter(trace_id=trace_id).adelete()
    # Checks are global; clear every row so an earlier case cannot match this span.
    await DeterministicCheck.objects.all().adelete()


async def test_waits_for_root_then_scores() -> dict:
    trace_id = "det-wait-root"
    await reset(trace_id)
    await DeterministicCheck.objects.acreate(
        name="has_parent",
        source="def evaluate(span):\n    return span.parent is not None\n",
        kind="tool",
    )
    child = make_span(
        span_id="child-1",
        trace_id=trace_id,
        parent_id="root-1",
        kind="tool",
        name="search",
        attributes={"input": "q", "output": "r"},
    )
    await child.asave()

    early = await score_span(child)
    assert "score" not in early.eval_attributes, early.eval_attributes

    root = make_span(
        span_id="root-1",
        trace_id=trace_id,
        kind="workflow",
        name="book",
        attributes={"input": "book a flight", "output": "ok", "intent": "book"},
    )
    await root.asave()

    scored = await score_span(await Span.objects.aget(span_id="child-1"))
    evaluators = scored.eval_attributes["evaluators"]
    assert evaluators["jev"]["kind"] == "llm_judge"
    assert evaluators["jev"]["score"] is None
    assert evaluators["jev"]["skipped"] == "missing expectations"
    assert evaluators["has_parent"]["kind"] == "deterministic"
    assert evaluators["has_parent"]["score"] == 1.0
    assert evaluators["has_parent"]["metrics"][0]["verdict"] == "yes"
    assert scored.eval_attributes["score"] == 1.0
    return scored.eval_attributes


async def test_graph_and_contracts() -> dict:
    trace_id = "det-graph"
    await reset(trace_id)
    await DeterministicCheck.objects.acreate(
        name="graph_ok",
        source=(
            "def evaluate(span):\n"
            "    ok = span.parent is not None and len(span.siblings) == 1\n"
            "    ok = ok and len(span.parent.children) == 2\n"
            "    ok = ok and span.attributes.get('flag') == 'yes'\n"
            "    return ok\n"
        ),
    )
    await DeterministicCheck.objects.acreate(
        name="score_pair",
        source='def evaluate(span):\n    return 0.75, "partial credit"\n',
    )
    await DeterministicCheck.objects.acreate(
        name="abstain_me",
        source="def evaluate(span):\n    return None\n",
    )
    await DeterministicCheck.objects.acreate(
        name="tool_only",
        source="def evaluate(span):\n    return True\n",
        kind="tool",
    )

    root = make_span(span_id="g-root", trace_id=trace_id, name="root", kind="workflow")
    left = make_span(
        span_id="g-left",
        trace_id=trace_id,
        parent_id="g-root",
        name="left",
        attributes={"flag": "yes", "input": "a", "output": "b"},
    )
    right = make_span(
        span_id="g-right",
        trace_id=trace_id,
        parent_id="g-root",
        name="right",
        attributes={"flag": "no"},
    )
    await Span.objects.abulk_create([root, left, right])

    scored = await score_span(await Span.objects.aget(span_id="g-left"))
    evaluators = scored.eval_attributes["evaluators"]
    assert set(evaluators) == {"jev", "graph_ok", "score_pair", "abstain_me"}
    assert evaluators["graph_ok"]["score"] == 1.0
    assert evaluators["score_pair"]["score"] == 0.75
    assert evaluators["score_pair"]["metrics"][0]["reason"] == "partial credit"
    assert evaluators["abstain_me"]["score"] is None
    assert evaluators["abstain_me"]["metrics"][0]["verdict"] == "abstain"
    assert scored.eval_attributes["score"] == (1.0 + 0.75) / 2
    return scored.eval_attributes


async def test_llm_judge_and_check_on_one_span() -> dict:
    trace_id = "det-mixed"
    await reset(trace_id)
    await DeterministicCheck.objects.acreate(
        name="status_ok",
        source='def evaluate(span):\n    return span.status == "ok"\n',
    )
    span = make_span(
        span_id="mixed-1",
        trace_id=trace_id,
        kind="llm",
        name="answer",
        attributes={
            "intent": "answer the user",
            "input": "hi",
            "output": "hello",
            "expectations": ["is polite", "is short"],
        },
    )
    await span.asave()

    async def fake_score(self, span, expectation):
        return Metric(expectation, score=0.5, verdict="partial")

    async def fake_explain(self, state, metric):
        return "stubbed reason"

    with (
        patch("innermind.span_processor.JevEvaluator.score", fake_score),
        patch("innermind.span_processor.JevEvaluator.explain", fake_explain),
    ):
        scored = await score_span(await Span.objects.aget(span_id="mixed-1"))

    evaluators = scored.eval_attributes["evaluators"]
    jev = evaluators["jev"]
    assert jev["kind"] == "llm_judge"
    assert [m["expectation"] for m in jev["metrics"]] == ["is polite", "is short"]
    assert jev["score"] == 0.5
    assert jev["metrics"][0]["reason"] == "stubbed reason"
    assert evaluators["status_ok"]["kind"] == "deterministic"
    assert evaluators["status_ok"]["score"] == 1.0
    assert scored.eval_attributes["score"] == (0.5 + 1.0) / 2
    return scored.eval_attributes


async def test_sandbox_escapes() -> dict:
    trace_id = "det-escape"
    cases = {
        "import_os": "import os\ndef evaluate(span):\n    return True\n",
        "dunder_class": "def evaluate(span):\n    return ().__class__\n",
        "open_call": "def evaluate(span):\n    open('/etc/passwd')\n    return True\n",
        "mutate": "def evaluate(span):\n    span.attributes['x'] = 1\n    return True\n",
        "loop": "def evaluate(span):\n    while True:\n        pass\n",
    }
    await reset(trace_id)
    for name, source in cases.items():
        await DeterministicCheck.objects.acreate(name=name, source=source)
    await DeterministicCheck.objects.acreate(
        name="still_runs", source="def evaluate(span):\n    return True\n"
    )
    await make_span(span_id="esc-root", trace_id=trace_id, name="root").asave()

    assert audit(cases["import_os"])
    assert audit(cases["dunder_class"])
    assert audit(cases["open_call"])

    with patch("innermind.deterministic.WALL_SECONDS", 2):
        scored = await score_span(await Span.objects.aget(span_id="esc-root"))

    evaluators = scored.eval_attributes["evaluators"]
    for name in cases:
        assert evaluators[name]["score"] is None, name
        assert evaluators[name]["metrics"][0]["error"], f"{name}: {evaluators[name]}"
    assert "item assignment" in evaluators["mutate"]["metrics"][0]["error"]
    assert "longer than" in evaluators["loop"]["metrics"][0]["error"]
    # Each check runs in its own child, so a runaway script cannot sink its neighbours.
    assert evaluators["still_runs"]["score"] == 1.0
    return scored.eval_attributes


# template: (blank constant, filled constant, span it is scored on, expected score)
CONFIGURED = {
    "exact_match": ('REFERENCE = ""', 'REFERENCE = "Sent"', "s-email", 1.0),
    "contains": ('NEEDLE = ""', 'NEEDLE = "tp1351"', "s-root", 1.0),
    "regex": ('PATTERN = ""', "PATTERN = r'\"price\": \\d+'", "s-root", 1.0),
    "goal_state_match": ("GOAL_MARKERS = ()", 'GOAL_MARKERS = ("TP1351", "Lisbon")', "s-root", 0.0),
    "json_required_keys": (
        "REQUIRED_KEYS = ()",
        'REQUIRED_KEYS = ("flight", "price")',
        "s-root",
        1.0,
    ),
    "schema_field_conformance": (
        "FIELDS = {}",
        'FIELDS = {"price": {"type": "number", "max": 100}, "seat": {"type": "string"}}',
        "s-root",
        0.0,
    ),
    "tool_args_match": (
        "EXPECTED_CALLS = ()",
        'EXPECTED_CALLS = ({"name": "search_flights", "arguments": {"destination": "LIS"}},)',
        "s-root",
        1.0,
    ),
    "llm_call_budget": ("MAX_LLM_CALLS = None", "MAX_LLM_CALLS = 0", "s-root", 0.0),
    "latency_budget": ("MAX_SECONDS = None", "MAX_SECONDS = 2", "s-choose", 1.0),
    "cost_budget": ("MAX_COST = None", "MAX_COST = 0.001", "s-choose", 0.0),
}


async def test_seeded_checks() -> dict:
    trace_id = "det-seeded"
    await reset(trace_id)
    assert await sync_to_async(checks.seed)(DeterministicCheck) == len(checks.CATALOG)
    seeded = {c.name: c async for c in DeterministicCheck.objects.all()}
    assert {name for name, c in seeded.items() if c.enabled} == set(checks.READY)

    edited = seeded["regex"]
    edited.source = "def evaluate(span):\n    return None\n"
    await edited.asave()
    assert await sync_to_async(checks.seed)(DeterministicCheck) == 0
    assert (await DeterministicCheck.objects.aget(name="regex")).source == edited.source
    await DeterministicCheck.objects.filter(name="regex").adelete()
    await sync_to_async(checks.seed)(DeterministicCheck)

    # Unconfigured templates run too: each must abstain, never error or score.
    await DeterministicCheck.objects.aupdate(enabled=True)

    root = make_span(
        span_id="s-root",
        trace_id=trace_id,
        kind="workflow",
        name="book",
        attributes={
            "overmind.intent": "book the cheapest flight",
            "overmind.input": "London to Lisbon on Friday",
            "overmind.output": '{"flight": "TP1351", "price": 120}',
            "overmind.reference_trajectory": ["search_flights", "book_flight"],
        },
    )
    search = make_span(
        span_id="s-search",
        trace_id=trace_id,
        parent_id="s-root",
        kind="tool",
        name="search_flights",
        attributes={
            "overmind.input": '{"origin": "LON", "destination": "LIS"}',
            "overmind.output": '[{"flight": "TP1351"',
        },
    )
    email = make_span(
        span_id="s-email",
        trace_id=trace_id,
        parent_id="s-root",
        kind="tool",
        name="send_email",
        attributes={"overmind.input": "ops@example.com", "overmind.output": "sent"},
    )
    choose = make_span(
        span_id="s-choose",
        trace_id=trace_id,
        parent_id="s-root",
        kind="llm",
        name="choose",
        attributes={
            "overmind.output": 'Picked:\n```json\n{"pick": "TP1351"}\n```',
            "genai.elapsed_seconds": 1.2,
            "genai.cost": 0.002,
        },
    )
    await Span.objects.abulk_create([root, search, email, choose])

    observed = {}
    templates_ran = set()
    for span_id in ("s-root", "s-search", "s-email", "s-choose"):
        scored = await score_span(await Span.objects.aget(span_id=span_id))
        evaluators = scored.eval_attributes["evaluators"]
        for name in set(checks.TEMPLATES) & set(evaluators):
            metric = evaluators[name]["metrics"][0]
            assert metric["verdict"] == "abstain" and not metric["error"], (span_id, name, metric)
            templates_ran.add(name)
        observed[span_id] = scored.eval_attributes
    assert templates_ran == set(checks.TEMPLATES)

    root_evals = observed["s-root"]["evaluators"]
    assert root_evals["output_is_valid_json"]["score"] == 1.0
    assert root_evals["tool_selection"]["score"] == 0.5
    assert "book_flight" in root_evals["tool_selection"]["metrics"][0]["reason"]
    assert observed["s-root"]["score"] == 0.75

    truncated = observed["s-search"]["evaluators"]["output_is_valid_json"]
    assert truncated["score"] == 0.0
    assert observed["s-search"]["evaluators"]["tool_selection"]["score"] is None

    assert observed["s-email"]["evaluators"]["output_is_valid_json"]["score"] is None
    assert observed["s-email"]["score"] is None

    assert observed["s-choose"]["evaluators"]["output_is_valid_json"]["score"] == 1.0

    assert set(CONFIGURED) == set(checks.TEMPLATES)
    await DeterministicCheck.objects.filter(name__in=checks.READY).adelete()
    for name, (blank, filled, _, _) in CONFIGURED.items():
        source = checks.source(name)
        assert blank in source, name
        await DeterministicCheck.objects.filter(name=name).aupdate(
            source=source.replace(blank, filled)
        )
    configured = {}
    for span_id in {span_id for _, _, span_id, _ in CONFIGURED.values()}:
        scored = await score_span(await Span.objects.aget(span_id=span_id))
        configured[span_id] = scored.eval_attributes["evaluators"]
    for name, (_, _, span_id, score) in CONFIGURED.items():
        result = configured[span_id][name]
        assert result["score"] == score, (name, result)
    observed["configured_templates"] = configured
    return observed


async def main() -> int:
    RUNS.mkdir(parents=True, exist_ok=True)
    results = {}
    try:
        results["waits_for_root"] = await test_waits_for_root_then_scores()
        results["graph_and_contracts"] = await test_graph_and_contracts()
        results["llm_judge_and_check_on_one_span"] = await test_llm_judge_and_check_on_one_span()
        results["sandbox_escapes"] = await test_sandbox_escapes()
        results["seeded_checks"] = await test_seeded_checks()
    finally:
        artifact = {
            "command": COMMAND,
            "wall_seconds_default": WALL_SECONDS,
            "environment": {"OPENROUTER_API_KEY": "set (Jev calls stubbed)"},
            "observed": results,
            "ok": len(results) == 5,
        }
        ARTIFACT.write_text(json.dumps(artifact, indent=2, default=str) + "\n")
        print(f"wrote {ARTIFACT}")

    print("all evaluator checks passed")
    return 0


if __name__ == "__main__":
    ensure_schema()
    sys.exit(asyncio.run(main()))
