"""A project's spans cannot be rewritten through another project's OTLP export."""

import uuid

import pytest
from factories import api_key_client, ingest_spans, make_project, make_user
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.resource.v1.resource_pb2 import Resource
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans
from opentelemetry.proto.trace.v1.trace_pb2 import Span as ProtoSpan

from overbae.models import Span

pytestmark = pytest.mark.django_db


def test_a_reused_span_id_from_another_project_leaves_the_owner_row_untouched():
    owner, other = make_project(), make_project()
    span_id = uuid.uuid4().hex[:16]
    ingest_spans(owner, [{"name": "owner.run", "attributes": {"k": "owner"}, "span_id": span_id}])

    response = ingest_spans(
        other,
        [
            {"name": "other.run", "attributes": {"k": "other"}, "span_id": span_id},
            {"name": "other.fresh", "attributes": {}},
        ],
        resource={"service.name": "other-service"},
    )

    assert response.json()["spans_rejected"] == 1
    span = Span.objects.get(span_id=span_id)
    assert (span.project_id, span.name, span.attributes["k"]) == (owner.id, "owner.run", "owner")
    assert span.service_name == ""
    assert Span.objects.filter(project=other, name="other.fresh").exists()


def test_the_owner_can_still_re_export_its_own_span():
    project = make_project()
    span_id = uuid.uuid4().hex[:16]
    ingest_spans(project, [{"name": "run", "attributes": {"k": "first"}, "span_id": span_id}])
    response = ingest_spans(
        project, [{"name": "run", "attributes": {"k": "second"}, "span_id": span_id}]
    )

    assert response.json()["spans_rejected"] == 0
    assert Span.objects.get(span_id=span_id).attributes["k"] == "second"


def test_one_export_naming_the_same_new_span_for_two_projects_keeps_the_first():
    user = make_user()
    first, second = make_project(member=user), make_project(member=user)
    span_id = uuid.uuid4().hex[:16]

    def group(project, name):
        resource = Resource(
            attributes=[
                KeyValue(key="overmind.project.id", value=AnyValue(string_value=str(project.id)))
            ]
        )
        span = ProtoSpan(
            trace_id=bytes.fromhex(uuid.uuid4().hex),
            span_id=bytes.fromhex(span_id),
            name=name,
            start_time_unix_nano=1_000,
            end_time_unix_nano=9_000,
        )
        return ResourceSpans(resource=resource, scope_spans=[ScopeSpans(spans=[span])])

    request = ExportTraceServiceRequest(
        resource_spans=[group(first, "first.run"), group(second, "second.run")]
    )
    response = api_key_client(user).post(
        "/api/v1/traces",
        data=request.SerializeToString(),
        content_type="application/x-protobuf",
    )

    assert response.status_code == 200, response.content
    assert response.json()["spans_rejected"] == 1
    span = Span.objects.get(span_id=span_id)
    assert (span.project_id, span.name) == (first.id, "first.run")
