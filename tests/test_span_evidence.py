from types import SimpleNamespace

from overbae.services.eval.evaluators.base import EvalUnit, resolve_variables
from overbae.services.eval.normalizer import reconstruct_spans
from overbae.services.eval.rubric_compiler import build_judge_prompt
from overbae.services.eval.span_evidence import (
    build_span_tree,
    produced_identity_fields,
    render_span_tree,
    unwrap_payload,
)


def _span(**kwargs):
    defaults = {
        "span_id": "1",
        "trace_id": "t" * 32,
        "parent_span_id": None,
        "span_type": "tool_call",
        "name": "workshop.tool.mcp",
        "duration_ns": 1_000_000,
        "status_code": 1,
        "attributes": {},
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def test_span_tree_clips_oversized_payload_before_parsing():
    """A 70MB+ DOM snapshot in one attribute, parsed into objects and re-dumped
    on every judge render, OOM-killed the scoring worker — the tree clips it
    before it ever parses."""
    giant = '{"dom_state": {"node": "' + "x" * 300_000 + '"}}'
    spans = [
        _span(
            span_id="ctx",
            span_type="function",
            name="Agent._prepare_context",
            attributes={"inputs": "{}", "outputs": giant},
        )
    ]
    (node,) = build_span_tree(spans)
    assert isinstance(node["outputs"], str)
    assert len(node["outputs"]) <= 100_000 + len("…[payload truncated]")
    assert node["outputs"].endswith("…[payload truncated]")


def test_span_tree_parses_normal_payloads_unchanged():
    spans = [
        _span(
            span_id="ok",
            span_type="function",
            attributes={"outputs": '{"result": "fine"}'},
        )
    ]
    (node,) = build_span_tree(spans)
    assert node["outputs"] == {"result": "fine"}


def test_unwrap_cursor_mcp_envelope_exposes_rows():
    envelope = {
        "status": "success",
        "value": {
            "content": [{"text": {"text": '{"rows": [{"id": "1"}], "n": 1}'}}],
            "isError": False,
        },
    }
    assert unwrap_payload(envelope) == {"rows": [{"id": "1"}], "n": 1}


def test_unwrap_empty_inner_text_is_empty_not_wrapper():
    envelope = {
        "status": "success",
        "value": {"content": [{"text": {"text": ""}}], "isError": False},
    }
    assert unwrap_payload(envelope) in ("", None)


def test_span_tree_recovers_mcp_tool_name_and_peeled_rows():
    spans = [
        _span(
            span_id="root",
            name="workshop.llm_round",
            span_type="llm_call",
            attributes={"inputs": "[]", "outputs": "[]"},
        ),
        _span(
            span_id="mcp1",
            parent_span_id="root",
            attributes={
                "tool.name": "mcp",
                "inputs": {
                    "providerIdentifier": "workshop",
                    "toolName": "sample_rows",
                    "args": {"n": 20},
                },
                "outputs": {
                    "status": "success",
                    "value": {"content": [{"text": {"text": '{"rows": [1, 2], "n": 2}'}}]},
                },
            },
        ),
    ]
    tree = build_span_tree(spans)
    assert tree[0]["name"] == "workshop.llm_round"
    child = tree[0]["children"][0]
    assert child["tool"] == "sample_rows"
    assert child["inputs"] == {"n": 20}
    assert child["outputs"] == {"rows": [1, 2], "n": 2}
    rendered = render_span_tree(tree)
    assert "sample_rows" in rendered
    assert "n" in rendered


def test_reconstruct_spans_attaches_span_tree():
    llm = _span(
        span_id="llm",
        span_type="llm_call",
        name="chat",
        attributes={
            "inputs": [{"role": "user", "content": "hi"}],
            "outputs": [{"role": "assistant", "content": "ok"}],
        },
    )
    traj = reconstruct_spans([llm])
    assert traj["span_tree"]
    assert traj["span_tree"][0]["id"] == "llm"


def test_binding_finds_rows_on_span_tree_not_chatml_wrapper():
    unit = EvalUnit(
        trajectory={
            "messages": [
                {
                    "role": "tool",
                    "content": '{"status":"success","value":{"content":[{"text":{"text":""}}]}}',
                }
            ],
            "span_tree": [
                {
                    "name": "workshop.tool.mcp",
                    "tool": "sample_rows",
                    "outputs": {"rows": [{"id": "40767A"}], "n": 1},
                    "children": [],
                }
            ],
            "final_output": "analysis",
        }
    )
    resolved = resolve_variables(unit, [{"var": "rows", "source": "span_tree"}])
    assert "40767A" in resolved["rows"]


def test_judge_prompt_defaults_to_span_tree():
    ev = SimpleNamespace(
        rubric_md="grade it",
        checklist=[{"id": "a", "q": "ok?", "weight": 1}],
        score_type="numeric",
        score_min=0,
        score_max=1,
        choices=None,
        config={},
    )
    prompt = build_judge_prompt(ev, {"output": "hi"}, span_tree="- workshop.tool.mcp → sample_rows")
    assert "span_tree (full execution graph" in prompt
    assert "sample_rows" in prompt
    assert prompt.index("span_tree") < prompt.index("output:")


def test_judge_prompt_omits_trajectory_when_span_tree_rendered():
    ev = SimpleNamespace(
        rubric_md="grade it",
        checklist=[{"id": "a", "q": "ok?", "weight": 1}],
        score_type="numeric",
        score_min=0,
        score_max=1,
        choices=None,
        config={},
    )
    huge = "x" * 50_000
    prompt = build_judge_prompt(
        ev,
        {"trajectory": huge, "output": "hi"},
        span_tree="- tool → act",
    )
    assert "trajectory:\n" not in prompt
    assert "output:\nhi" in prompt


def test_produced_identity_fields_from_nested_outputs():
    tree = [
        {
            "name": "write",
            "inputs": {"purpose": "north-kind"},
            "outputs": {
                "status": "ok",
                "value": {"name": "alpha-set", "intent": "south-kind", "n": 3},
            },
            "children": [],
        }
    ]
    found = produced_identity_fields(tree)
    assert {"purpose": "north-kind"} in found
    assert any(
        item.get("intent") == "south-kind" and item.get("name") == "alpha-set" for item in found
    )


def test_produced_identity_ignores_type_only_wrappers():
    tree = [{"outputs": {"type": "text", "text": "hi"}, "children": []}]
    assert produced_identity_fields(tree) == []


def test_span_tree_sorts_siblings_by_start_time_not_input_order():
    """``subtree_spans`` and unscored DB rows arrive DFS / insertion order; judges
    read the rendered tree as execution order."""
    entry = _span(
        span_id="entry",
        span_type="entry_point",
        name="run",
        start_time_ns=1,
    )
    weather = _span(
        span_id="w1",
        parent_span_id="entry",
        name="get_weather",
        start_time_ns=10,
        attributes={"tool.name": "get_weather", "inputs": {"city": "Paris"}},
    )
    diff = _span(
        span_id="d1",
        parent_span_id="entry",
        name="calculate_temperature_difference",
        start_time_ns=30,
        attributes={
            "tool.name": "calculate_temperature_difference",
            "inputs": {"a": 14, "b": 27},
        },
    )
    # calculate before get_weather in the input list — the bug shape.
    tree = build_span_tree([entry, diff, weather])
    child_names = [c["name"] for c in tree[0]["children"]]
    assert child_names == ["get_weather", "calculate_temperature_difference"]
    rendered = render_span_tree(tree)
    assert rendered.index("get_weather") < rendered.index("calculate_temperature_difference")
