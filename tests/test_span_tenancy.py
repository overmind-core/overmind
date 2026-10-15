"""A project's spans cannot be rewritten through another project's OTLP export."""

import uuid

import pytest
from factories import ingest_spans, make_project

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
