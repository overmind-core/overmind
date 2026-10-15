import json
import uuid

import pytest
import requests
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status

from overbae.innermind.checks import render

from .conftest import GOOD_REPLY
from .stack import drain

EXPECTATION = "The reply quotes the order id and its status."
RUN_SIGNALS = "overbae.tasks.innermind.run_signals"
SWEEP_SPANS = "overbae.tasks.innermind.sweep_unscored_spans"
SWEEP_TRAJECTORIES = "overbae.tasks.innermind.score_trajectories"
# Past the span sweep's grace period, so every beat re-drives what is still unscored.
SWEEP_WAIT = 120


@pytest.fixture
def project(cli, mcp_for, rest_for, sample_agent, worker, judges):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    key = cli.project_key(sample_agent)
    return mcp_for(key), rest_for(key), key


def _attrs(**values) -> list[KeyValue]:
    return [
        KeyValue(key=key.replace("__", "."), value=AnyValue(string_value=str(value)))
        for key, value in values.items()
    ]


class Trace:
    """One OTLP trace: a workflow root that answers a ticket and its tool calls."""

    def __init__(self, capability_id: str, reply: str, tools: list[bool]):
        self.trace_id = uuid.uuid4().bytes
        self.root_id = uuid.uuid4().bytes[:8]
        self.capability_id = capability_id
        self.reply = reply
        self.tools = []
        for ok in tools:
            self.add_tool(ok)

    def add_tool(self, ok: bool) -> None:
        self.tools.append((uuid.uuid4().bytes[:8], ok))

    @property
    def hex(self) -> str:
        return self.trace_id.hex()

    def request(self, *, root: bool = True, tools: bool = True) -> ExportTraceServiceRequest:
        root_span = Span(
            trace_id=self.trace_id,
            span_id=self.root_id,
            name="handle_ticket",
            start_time_unix_nano=1_000,
            end_time_unix_nano=90_000,
            attributes=_attrs(
                overmind__capability__id=self.capability_id,
                overmind__span_type="workflow",
                overmind__input="Refund order 42 please",
                overmind__output=self.reply,
                overmind__expectations=json.dumps([EXPECTATION]),
            ),
        )
        tool_spans = [
            Span(
                trace_id=self.trace_id,
                span_id=span_id,
                parent_span_id=self.root_id,
                name="lookup_order",
                start_time_unix_nano=2_000 + i * 10_000,
                end_time_unix_nano=9_000 + i * 10_000,
                status=Status(code=Status.STATUS_CODE_OK if ok else Status.STATUS_CODE_ERROR),
                attributes=_attrs(
                    overmind__capability__id=self.capability_id,
                    overmind__span_type="tool_call",
                    gen_ai__tool__name="lookup_order",
                    overmind__input=f'{{"order_id": "42", "attempt": {i}}}',
                    overmind__output="delivered" if ok else "",
                ),
            )
            for i, (span_id, ok) in enumerate(self.tools)
        ]
        spans = [*([root_span] if root else []), *(tool_spans if tools else [])]
        return ExportTraceServiceRequest(
            resource_spans=[ResourceSpans(scope_spans=[ScopeSpans(spans=spans)])]
        )

    def send(self, api_url: str, key: str, *, root: bool = True, tools: bool = True) -> None:
        response = requests.post(
            f"{api_url}/api/v1/traces",
            data=self.request(root=root, tools=tools).SerializeToString(),
            headers={"Content-Type": "application/x-protobuf", "X-Api-Key": key},
            timeout=30,
        )
        assert response.status_code == 200, response.text


def test_an_ingested_trace_is_scored_once_per_evidence_with_kept_history(
    project, live_api, worker, fake_llm
):
    mcp, rest, key = project
    answer = mcp.capability("answer")
    good = Trace(answer["id"], GOOD_REPLY, tools=[True])
    bad = Trace(answer["id"], "Your refund is on its way.", tools=[True])
    good.send(live_api.url, key)
    bad.send(live_api.url, key)
    drain(worker)

    roots = {
        row["trace_id"]: row
        for row in mcp.call("query_traces", {"capability": "answer", "limit": 10})["traces"]
    }
    assert roots[good.hex]["eval_score"] > roots[bad.hex]["eval_score"]
    assert roots[good.hex]["trajectory_score"] > roots[bad.hex]["trajectory_score"]

    detail = rest.request(
        "GET", f"/api/traces/{good.hex}/", params={"project": mcp.project()["id"]}
    )
    root = next(s for s in detail["spans"] if s["parent_span_id"] is None)
    expectation = root["eval_attributes"]["metrics"][EXPECTATION]
    assert expectation["verdict"] == "yes"
    assert expectation["score"] == 1.0
    assert root["eval_attributes"]["score"] == roots[good.hex]["eval_score"]

    def history(trace: Trace) -> list[dict]:
        page = rest.request(
            "GET",
            "/api/eval-scores/",
            params={"project": mcp.project()["id"], "trace_id": trace.hex, "page_size": 100},
        )
        return page["results"]

    first = history(good)
    assert {row["source_type"] for row in first} == {"span", "trajectory"}

    judged = len(fake_llm.decisions)
    good.send(live_api.url, key)
    drain(worker)
    assert history(good) == first
    assert len(fake_llm.decisions) == judged

    good.reply = "Your refund is on its way."
    good.send(live_api.url, key)
    drain(worker)
    assert len(history(good)) > len(first)

    health = mcp.call("inspect_capability_health", {"capability": "answer"})
    assert health["scoring"]["scored"] >= 2
    assert any(m["metric"] == EXPECTATION for m in health["scoring"]["metrics"])


def _history(rest, project_id: str, trace: Trace) -> list[dict]:
    return rest.request(
        "GET",
        "/api/eval-scores/",
        params={"project": project_id, "trace_id": trace.hex, "page_size": 100},
    )["results"]


def _spans(rest, project_id: str, trace: Trace) -> dict[str, dict]:
    detail = rest.request("GET", f"/api/traces/{trace.hex}/", params={"project": project_id})
    return {span["span_id"]: span for span in detail["spans"]}


def test_spans_are_scored_as_they_land_without_waiting_for_the_root(
    project, live_api, worker, fake_llm
):
    mcp, rest, key = project
    project_id = mcp.project()["id"]
    trace = Trace(mcp.capability("answer")["id"], GOOD_REPLY, tools=[True, False])

    trace.send(live_api.url, key, root=False)
    drain(worker)
    tool_ids = [span_id.hex() for span_id, _ in trace.tools]
    before = {
        span_id: span["eval_attributes"]
        for span_id, span in _spans(rest, project_id, trace).items()
    }
    assert set(before) == set(tool_ids)
    assert all("score" in attributes for attributes in before.values())
    assert _history(rest, project_id, trace)

    trace.send(live_api.url, key, tools=False)
    drain(worker)
    spans = _spans(rest, project_id, trace)
    assert {span_id: spans[span_id]["eval_attributes"] for span_id in tool_ids} == before
    root = spans[trace.root_id.hex()]["eval_attributes"]
    assert root["trajectory"]["span_count"] == 3
    trajectories = [
        row for row in _history(rest, project_id, trace) if row["source_type"] == "trajectory"
    ]
    assert len(trajectories) == 1


def test_a_failing_judge_is_tried_three_times_then_scored_with_errors(
    project, live_api, worker, fake_llm, beat, clock
):
    mcp, rest, key = project
    project_id = mcp.project()["id"]
    fake_llm.fail(lambda request: request.url.rstrip("/").endswith("/systemone"))

    def judge_calls() -> int:
        return sum(r.url.rstrip("/").endswith("/systemone") for r in fake_llm.requests)

    trace = Trace(mcp.capability("answer")["id"], GOOD_REPLY, tools=[True])
    trace.send(live_api.url, key)
    drain(worker)
    per_pass = judge_calls()
    assert per_pass > 0

    for _ in range(5):
        clock.shift(SWEEP_WAIT)
        beat(SWEEP_SPANS, SWEEP_TRAJECTORIES)
    # Three span passes over the root's rows, then three trajectory passes of path_judge.
    assert judge_calls() == 3 * per_pass + 3

    root = _spans(rest, project_id, trace)[trace.root_id.hex()]
    attributes = root["eval_attributes"]
    assert {m["error"] for m in attributes["metrics"].values() if m["type"] == "llm"} == {
        "scoring failed after 3 attempts"
    }
    path_judge = attributes["trajectory"]["metrics"]["path_judge"]
    assert path_judge["error"] == "scoring failed after 3 attempts"
    assert {row["source_type"] for row in _history(rest, project_id, trace)} == {
        "span",
        "trajectory",
    }

    clock.shift(SWEEP_WAIT)
    beat(SWEEP_SPANS, SWEEP_TRAJECTORIES)
    assert judge_calls() == 3 * per_pass + 3


def test_scored_spans_trajectories_and_drift_verdicts_are_never_judged_again(
    project, live_api, worker, fake_llm, beat, clock
):
    mcp, rest, key = project
    project_id = mcp.project()["id"]
    mcp.call("set_signal_subscription", {"signal_type": "plan_drift", "enabled": True})

    trace = Trace(mcp.capability("answer")["id"], GOOD_REPLY, tools=[True])
    trace.send(live_api.url, key)
    drain(worker)
    beat(RUN_SIGNALS)
    tool_id = trace.tools[0][0].hex()
    # The judges meet an expectation only on the good reply, so the tool step drifts.
    assert _spans(rest, project_id, trace)[tool_id]["eval_attributes"]["drift"] == 0.0

    judged = len(fake_llm.decisions)
    clock.shift(SWEEP_WAIT)
    beat(RUN_SIGNALS, SWEEP_SPANS, SWEEP_TRAJECTORIES)
    assert len(fake_llm.decisions) == judged

    trace.add_tool(True)
    trace.send(live_api.url, key)
    drain(worker)
    beat(RUN_SIGNALS)
    spans = _spans(rest, project_id, trace)
    assert spans[trace.tools[1][0].hex()]["eval_attributes"]["drift"] == 0.0
    assert spans[trace.root_id.hex()]["eval_attributes"]["trajectory"]["span_count"] == 2
    trajectories = [
        row for row in _history(rest, project_id, trace) if row["source_type"] == "trajectory"
    ]
    assert len(trajectories) == 1

    judged = len(fake_llm.decisions)
    clock.shift(SWEEP_WAIT)
    beat(RUN_SIGNALS, SWEEP_SPANS, SWEEP_TRAJECTORIES)
    assert len(fake_llm.decisions) == judged


def test_default_signals_raise_deduplicated_events_seen_on_every_surface(
    project, live_api, worker, beat
):
    mcp, rest, key = project
    project_id = mcp.project()["id"]
    answer = mcp.capability("answer")

    checks = mcp.call("list_signals", {})["signals"]
    assert next(check for check in checks if check["signal_type"] == "tool_failure")["enabled"]

    failing = Trace(answer["id"], GOOD_REPLY, tools=[False, False, True])
    failing.send(live_api.url, key)
    drain(worker)
    beat(RUN_SIGNALS)
    beat(RUN_SIGNALS)

    events = rest.request(
        "GET",
        "/api/signal-events/",
        params={"project": project_id, "unseen": "true", "signal_type": "tool_failure"},
    )["results"]
    assert [(e["signal_type"], e["trace_id"]) for e in events] == [("tool_failure", failing.hex)]
    assert "lookup_order (2/3)" in events[0]["message"]

    listed = mcp.call("list_signals", {})
    assert listed["unseen_events"] >= 1
    over_mcp = mcp.read("overmind://signal-events")
    assert events[0]["id"] in [e["id"] for e in over_mcp["events"]]
    evidence = mcp.call("query_traces", {"signal_event": events[0]["id"]})
    assert [trace["trace_id"] for trace in evidence["traces"]] == [failing.hex]

    rest.request("POST", "/api/signal-events/mark-seen/", json={"project": project_id})
    assert mcp.call("list_signals", {})["unseen_events"] == 0


def test_json_checks_grade_the_outer_payload_even_when_strings_contain_fences(
    project, live_api, worker
):
    mcp, rest, key = project
    capability = mcp.capability("answer")["id"]
    definitions = [
        ("outer keys", "json_required_keys", {"REQUIRED_KEYS": ["history"]}),
        ("outer shape", "schema_field_conformance", {"FIELDS": {"history": {"type": "array"}}}),
        ("outer JSON", "output_is_valid_json", {}),
    ]
    for name, check, params in definitions:
        mcp.call(
            "create_eval_matrix_row",
            {
                "capability": capability,
                "name": name,
                "type": "deterministic",
                "source": render(check, params),
                "span_name": "handle_ticket",
            },
        )
    payload = json.dumps({"history": [], "answer": '```json\n{"value":1}\n```'})
    for reply, expected in [
        (payload, 1.0),
        ("```json\n" + payload + "\n```", 1.0),
        ('{"broken": ```json\n{"history":[]}\n```}', 0.0),
    ]:
        trace = Trace(capability, reply, tools=[])
        trace.send(live_api.url, key)
        drain(worker)
        metrics = _spans(rest, mcp.project()["id"], trace)[trace.root_id.hex()]["eval_attributes"][
            "metrics"
        ]
        for name, _, _ in definitions:
            assert metrics[name]["score"] == expected, metrics[name]
