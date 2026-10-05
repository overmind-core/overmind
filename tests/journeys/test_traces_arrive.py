import uuid

import pytest
import requests
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span

from .stack import drain


@pytest.fixture
def project(cli, mcp_for, sample_agent, worker):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    return mcp_for(cli.project_key(sample_agent))


def spans_of(mcp, trace_id: str) -> list[dict]:
    return mcp.call("query_traces", {"trace_id": trace_id, "all_spans": True, "limit": 100})[
        "traces"
    ]


def assert_one_rooted_tree(mcp, trace_id: str) -> dict:
    trace = mcp.read(f"overmind://traces/{trace_id}")
    spans = trace["spans"]
    ids = {span["span_id"] for span in spans}
    roots = [span for span in spans if span["parent_span_id"] is None]
    assert len(roots) == 1
    assert all(span["parent_span_id"] in ids for span in spans if span is not roots[0])
    return trace


def test_sdk_traces_arrive_as_one_rooted_tree_bound_to_capabilities(
    project, sample_agent, live_api, support_desk_llm, fake_llm, worker
):
    replies = sample_agent.run(
        "Refund order 42 please", api_url=live_api.url, llm_url=support_desk_llm
    )
    assert replies == ["Your refund is on its way. The order was delivered."]
    drain(worker)

    [root] = project.call("query_traces", {"limit": 100})["traces"]
    trace = assert_one_rooted_tree(project, root["trace_id"])
    assert trace["root"]["name"] == "handle_ticket"

    rows = spans_of(project, root["trace_id"])
    models = [row for row in rows if row["model"]]
    by_capability = {row["capability"] for row in rows if row["span_type"] == "llm_call"}
    assert by_capability == {"triage", "answer"}
    assert any(row["name"] == "lookup_order" and row["capability"] == "answer" for row in rows)

    agent_tokens = sum(
        r.total_tokens for r in fake_llm.requests if r.url.startswith("http://fake-llm")
    )
    assert root["total_tokens"] == agent_tokens
    assert models


def _attr(key: str, value) -> KeyValue:
    if isinstance(value, int):
        return KeyValue(key=key, value=AnyValue(int_value=value))
    return KeyValue(key=key, value=AnyValue(string_value=str(value)))


def test_any_otel_exporter_can_send_traces_without_the_sdk(project, live_api, cli, sample_agent):
    triage = project.capability("triage")
    trace_id = uuid.uuid4().bytes
    root_id, call_id = uuid.uuid4().bytes[:8], uuid.uuid4().bytes[:8]
    request = ExportTraceServiceRequest(
        resource_spans=[
            ResourceSpans(
                scope_spans=[
                    ScopeSpans(
                        spans=[
                            Span(
                                trace_id=trace_id,
                                span_id=root_id,
                                parent_span_id=b"\x00" * 8,
                                name="classify",
                                start_time_unix_nano=1_000,
                                end_time_unix_nano=9_000,
                            ),
                            Span(
                                trace_id=trace_id,
                                span_id=call_id,
                                parent_span_id=root_id,
                                name="chat openai/gpt-4.1-mini",
                                start_time_unix_nano=2_000,
                                end_time_unix_nano=8_000,
                                attributes=[
                                    _attr("overmind.capability.id", triage["id"]),
                                    _attr("gen_ai.system", "openai"),
                                    _attr("gen_ai.request.model", "openai/gpt-4.1-mini"),
                                    _attr("gen_ai.usage.input_tokens", 10),
                                    _attr("gen_ai.usage.output_tokens", 5),
                                ],
                            ),
                        ]
                    )
                ]
            )
        ]
    )
    response = requests.post(
        f"{live_api.url}/api/v1/traces",
        data=request.SerializeToString(),
        headers={
            "Content-Type": "application/x-protobuf",
            "X-Api-Key": cli.project_key(sample_agent),
        },
        timeout=30,
    )
    assert response.status_code == 200

    hex_id = trace_id.hex()
    assert_one_rooted_tree(project, hex_id)
    rows = {row["span_id"]: row for row in spans_of(project, hex_id)}
    assert rows[call_id.hex()]["capability"] == "triage"
    assert rows[root_id.hex()]["total_tokens"] == 15
