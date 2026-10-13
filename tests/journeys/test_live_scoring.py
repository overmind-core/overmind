import json
import uuid

import pytest
import requests
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status

from .conftest import GOOD_REPLY
from .stack import drain

EXPECTATION = "The reply quotes the order id and its status."
RUN_SIGNALS = "overbae.tasks.innermind.run_signals"


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
        self.tools = [(uuid.uuid4().bytes[:8], ok) for ok in tools]

    @property
    def hex(self) -> str:
        return self.trace_id.hex()

    def request(self) -> ExportTraceServiceRequest:
        root = Span(
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
        tools = [
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
        return ExportTraceServiceRequest(
            resource_spans=[ResourceSpans(scope_spans=[ScopeSpans(spans=[root, *tools])])]
        )

    def send(self, api_url: str, key: str) -> None:
        response = requests.post(
            f"{api_url}/api/v1/traces",
            data=self.request().SerializeToString(),
            headers={"Content-Type": "application/x-protobuf", "X-Api-Key": key},
            timeout=30,
        )
        assert response.status_code == 200, response.text


def test_an_ingested_trace_is_scored_per_span_then_as_a_trajectory_with_kept_history(
    project, live_api, worker
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

    good.send(live_api.url, key)
    drain(worker)
    again = history(good)
    assert len(again) == 2 * len(first)

    health = mcp.call("inspect_capability_health", {"capability": "answer"})
    assert health["scoring"]["scored"] >= 2
    assert any(m["metric"] == EXPECTATION for m in health["scoring"]["metrics"])


def test_a_subscribed_signal_raises_one_event_per_conversation_seen_on_every_surface(
    project, live_api, worker, beat
):
    mcp, rest, key = project
    project_id = mcp.project()["id"]
    answer = mcp.capability("answer")

    subscribed = mcp.call(
        "set_signal_subscription", {"signal_type": "tool_failure", "enabled": True}
    )
    assert subscribed["subscription"]["enabled"] is True

    failing = Trace(answer["id"], GOOD_REPLY, tools=[False, False, True])
    failing.send(live_api.url, key)
    drain(worker)
    beat(RUN_SIGNALS)
    beat(RUN_SIGNALS)

    events = rest.request(
        "GET", "/api/signal-events/", params={"project": project_id, "unseen": "true"}
    )["results"]
    assert [(e["signal_type"], e["trace_id"]) for e in events] == [("tool_failure", failing.hex)]
    assert "lookup_order (2/3)" in events[0]["message"]

    listed = mcp.call("list_signals", {})
    assert listed["unseen_events"] == 1
    over_mcp = mcp.read("overmind://signal-events")
    assert [e["id"] for e in over_mcp["events"]] == [events[0]["id"]]

    rest.request("POST", "/api/signal-events/mark-seen/", json={"project": project_id})
    assert mcp.call("list_signals", {})["unseen_events"] == 0
