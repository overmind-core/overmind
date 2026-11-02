import json
import uuid

import requests
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans, Span, Status

EXPECTATION = "The reply quotes the order id and its status."
RUN_SIGNALS = "overbae.tasks.innermind.run_signals"
SWEEP_SPANS = "overbae.tasks.innermind.sweep_unscored_spans"
SWEEP_TRAJECTORIES = "overbae.tasks.innermind.score_trajectories"
# Past the span sweep's grace period, so every beat re-drives what is still unscored.
SWEEP_WAIT = 120


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


def score_history(rest, project_id: str, trace: Trace) -> list[dict]:
    return rest.request(
        "GET",
        "/api/eval-scores/",
        params={"project": project_id, "trace_id": trace.hex, "page_size": 100},
    )["results"]


def trace_spans(rest, project_id: str, trace: Trace) -> dict[str, dict]:
    detail = rest.request("GET", f"/api/traces/{trace.hex}/", params={"project": project_id})
    return {span["span_id"]: span for span in detail["spans"]}
