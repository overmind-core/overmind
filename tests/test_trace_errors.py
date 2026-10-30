import base64
import json
import uuid

import pytest
from factories import api_key_client, make_member, make_project, make_span, make_user
from google.protobuf.json_format import ParseDict
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("wire_format", ["json", "protobuf"])
def test_captured_errors_survive_ingest_and_reach_trace_list_and_detail(wire_format):
    project, user = make_project(), make_user()
    make_member(user, project)
    client = api_key_client(user, project)
    trace_id, span_id = uuid.uuid4().hex, uuid.uuid4().hex[:16]
    message = "Model unavailable\n" + "provider detail " * 100
    stack = "Traceback (most recent call last):\n  request()\nNotFoundError: model unavailable"
    attrs = {
        "error.type": "not_found_error",
        "error.message": message,
        "http.response.status_code": 404,
        "gen_ai.provider.name": "anthropic",
        "anthropic.request.id": "req_fixture",
    }

    def attributes(values):
        return [
            {"key": key, "value": {"intValue" if isinstance(value, int) else "stringValue": value}}
            for key, value in values.items()
        ]

    export = {
        "resourceSpans": [
            {
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": trace_id,
                                "spanId": span_id,
                                "name": "anthropic.messages.create",
                                "startTimeUnixNano": "1000",
                                "endTimeUnixNano": "2000",
                                "status": {"code": 2, "message": "Provider request failed"},
                                "attributes": attributes(attrs),
                                "events": [
                                    {
                                        "name": "exception",
                                        "timeUnixNano": "1900",
                                        "attributes": attributes(
                                            {
                                                "exception.type": "NotFoundError",
                                                "exception.message": "The requested model is unavailable",
                                                "exception.stacktrace": stack,
                                            }
                                        ),
                                    }
                                ],
                            }
                        ]
                    }
                ]
            }
        ]
    }
    if wire_format == "protobuf":
        # Protobuf JSON uses base64 IDs; OTLP JSON uses hex IDs.
        span = export["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
        span["traceId"] = base64.b64encode(bytes.fromhex(trace_id)).decode()
        span["spanId"] = base64.b64encode(bytes.fromhex(span_id)).decode()
        body = ParseDict(export, ExportTraceServiceRequest()).SerializeToString()
        content_type = "application/x-protobuf"
    else:
        body, content_type = json.dumps(export), "application/json"
    response = client.post("/api/v1/traces", data=body, content_type=content_type)
    assert response.status_code == 200, response.content
    detail = client.get(f"/api/traces/{trace_id}/", {"project": str(project.pk)}).json()
    [span] = detail["spans"]
    assert span["status_message"] == "Provider request failed"
    assert span["attributes"]["error.message"] == message
    assert span["events"][0]["attributes"]["exception.stacktrace"] == stack
    errors = span["error_details"]
    assert errors["context"]["HTTP status"] == "404"
    assert errors["context"]["anthropic.request.id"] == "req_fixture"
    assert {record["message"] for record in errors["records"]} == {
        message,
        "Provider request failed",
        "The requested model is unavailable",
    }
    assert any(record["stacktrace"] == stack for record in errors["records"])
    [row] = client.get("/api/traces/", {"project": str(project.pk)}).json()["results"]
    assert "404" in row["error_summary"]
    assert "not_found_error" in row["error_summary"]
    assert "Model unavailable" in row["error_summary"]


@pytest.mark.parametrize(
    "attrs",
    [
        {"error.type": "not_found_error", "http.response.status_code": 404},
        {
            "error.type": "not_found_error",
            "http.response.status_code": None,
            "http.status_code": 404,
        },
        {},
    ],
)
def test_errors_without_messages_use_captured_context_without_inventing_a_cause(attrs):
    project, user = make_project(), make_user()
    make_member(user, project)
    trace_id = uuid.uuid4().hex
    make_span(project, trace_id=trace_id, status_code=2, attributes=attrs)
    client = api_key_client(user, project)
    [span] = client.get(f"/api/traces/{trace_id}/").json()["spans"]
    assert span["error_details"]["records"] == (
        []
        if not attrs
        else [
            {
                "source": "Attributes",
                "type": "not_found_error",
                "message": "",
                "stacktrace": "",
            }
        ]
    )
    assert span["error_details"]["summary"] == (
        "not_found_error · HTTP 404" if attrs else "No error message captured"
    )


def test_spans_without_error_status_report_no_error_despite_http_status_or_exception_event():
    project, user = make_project(), make_user()
    make_member(user, project)
    trace_id = uuid.uuid4().hex
    make_span(
        project,
        trace_id=trace_id,
        status_code=0,
        attributes={"http.response.status_code": 404},
        events=[
            {
                "name": "exception",
                "attributes": {"exception.type": "NotFound", "exception.message": "No route"},
            }
        ],
    )
    client = api_key_client(user, project)
    [span] = client.get(f"/api/traces/{trace_id}/").json()["spans"]
    assert span["error_details"] is None
    [row] = client.get("/api/traces/", {"project": str(project.pk)}).json()["results"]
    assert row["error_summary"] is None
