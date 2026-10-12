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

from innermind.deterministic import WALL_SECONDS, audit  # noqa: E402
from innermind.innermind import DeterministicCheck, Span, ensure_schema  # noqa: E402
from innermind.span_processor import Metric, score_span  # noqa: E402

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


async def main() -> int:
    RUNS.mkdir(parents=True, exist_ok=True)
    results = {}
    try:
        results["waits_for_root"] = await test_waits_for_root_then_scores()
        results["graph_and_contracts"] = await test_graph_and_contracts()
        results["llm_judge_and_check_on_one_span"] = await test_llm_judge_and_check_on_one_span()
        results["sandbox_escapes"] = await test_sandbox_escapes()
    finally:
        artifact = {
            "command": COMMAND,
            "wall_seconds_default": WALL_SECONDS,
            "environment": {"OPENROUTER_API_KEY": "set (Jev calls stubbed)"},
            "observed": results,
            "ok": len(results) == 4,
        }
        ARTIFACT.write_text(json.dumps(artifact, indent=2, default=str) + "\n")
        print(f"wrote {ARTIFACT}")

    print("all evaluator checks passed")
    return 0


if __name__ == "__main__":
    ensure_schema()
    sys.exit(asyncio.run(main()))
