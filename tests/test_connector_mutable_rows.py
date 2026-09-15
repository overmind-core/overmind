"""Ingest overwrites a span only when the provider row version advances.

Braintrust rewrites rows in place when async scoring or human review lands —
same id, higher _xact_id — so those updates must not be silently dropped.
"""

from __future__ import annotations

import uuid

import pytest

from overbae.models import ConnectorCredential, Project, Span
from overbae.services.connectors.braintrust.mapping import BRAINTRUST, rows_to_records
from overbae.services.connectors.langfuse.mapping import LANGFUSE
from overbae.services.connectors.langsmith.mapping import LANGSMITH, runs_to_records
from overbae.services.connectors.mapping import observations_to_span_dicts
from overbae.tasks import connector_sync

pytestmark = pytest.mark.django_db


@pytest.fixture
def credential():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    return ConnectorCredential.objects.create(
        project=project,
        name="BT",
        connector_type=ConnectorCredential.ConnectorType.BRAINTRUST,
        api_key="bt-st-key",
    )


@pytest.fixture(autouse=True)
def _no_trace_scoring(monkeypatch):
    scored = []
    monkeypatch.setattr(
        "overbae.api.otlp.enqueue_trace_scoring",
        lambda spans: scored.append(list(spans)),
    )
    return scored


def _span_dicts(credential, *, xact_id, scores=None, name="handler"):
    row = {
        "id": "span-1",
        "span_id": "s-1",
        "span_parents": [],
        "root_span_id": "trace-1",
        "is_root": True,
        "created": "2026-01-02T00:00:00+00:00",
        "_xact_id": xact_id,
        "span_attributes": {"name": name, "type": "task"},
        "metrics": {"start": 1767312000.0, "end": 1767312001.0},
        "scores": scores or {},
    }
    return observations_to_span_dicts(
        rows_to_records([row]),
        credential=credential,
        conventions=BRAINTRUST,
    )


def test_higher_xact_id_overwrites_the_stored_span(credential, _no_trace_scoring):
    created = connector_sync._upsert_spans(
        credential.project, _span_dicts(credential, xact_id=1000), credential=credential
    )
    assert created == 1

    again = connector_sync._upsert_spans(
        credential.project,
        _span_dicts(credential, xact_id=2000, scores={"quality": 0.9}, name="handler-reviewed"),
        credential=credential,
    )

    span = Span.objects.get(project=credential.project)
    assert again == 0  # an update is not a create
    assert span.name == "handler-reviewed"
    assert span.attributes["braintrust.scores"] == {"quality": 0.9}
    assert span.attributes["connector.version"] == "2000"


def test_same_or_lower_xact_id_leaves_the_span_alone(credential):
    connector_sync._upsert_spans(
        credential.project, _span_dicts(credential, xact_id=2000), credential=credential
    )
    connector_sync._upsert_spans(
        credential.project,
        _span_dicts(credential, xact_id=2000, name="same-version-different-name"),
        credential=credential,
    )
    connector_sync._upsert_spans(
        credential.project,
        _span_dicts(credential, xact_id=1, name="older"),
        credential=credential,
    )

    assert Span.objects.get(project=credential.project).name == "handler"


def test_an_overwrite_picks_up_a_renamed_credential(credential, _no_trace_scoring):
    connector_sync._upsert_spans(
        credential.project, _span_dicts(credential, xact_id=1000), credential=credential
    )
    span = Span.objects.get(project=credential.project)
    assert span.service_name == "braintrust/BT"

    credential.name = "Braintrust prod"
    connector_sync._upsert_spans(
        credential.project, _span_dicts(credential, xact_id=2000), credential=credential
    )

    span.refresh_from_db()
    assert span.service_name == "braintrust/Braintrust prod"


def test_an_update_does_not_re_enqueue_trace_scoring(credential, _no_trace_scoring):
    connector_sync._upsert_spans(
        credential.project, _span_dicts(credential, xact_id=1000), credential=credential
    )
    assert len(_no_trace_scoring) == 1

    connector_sync._upsert_spans(
        credential.project,
        _span_dicts(credential, xact_id=3000, scores={"quality": 1.0}),
        credential=credential,
    )

    # Rescoring an updated row would loop against a scorer that writes upstream.
    assert len(_no_trace_scoring) == 1


def test_a_provider_without_a_row_version_stays_insert_only(credential):
    from overbae.services.connectors.records import ObservationRecord

    record = ObservationRecord(
        id="lf-1",
        trace_id="t1",
        parent_observation_id=None,
        type="SPAN",
        name="first",
        start_time="2026-01-02T00:00:00Z",
        end_time="2026-01-02T00:00:01Z",
        is_root_observation=True,
    )
    connector_sync._upsert_spans(
        credential.project,
        observations_to_span_dicts([record], credential=credential, conventions=LANGFUSE),
        credential=credential,
    )
    record.name = "second"
    connector_sync._upsert_spans(
        credential.project,
        observations_to_span_dicts([record], credential=credential, conventions=LANGFUSE),
        credential=credential,
    )

    assert Span.objects.get(project=credential.project).name == "first"


def test_a_pending_langsmith_run_is_replaced_once_it_completes(credential, _no_trace_scoring):
    def _spans(*, end_time, name="handler"):
        run = {
            "id": "run-1",
            "trace_id": "trace-1",
            "parent_run_ids": [],
            "is_root": True,
            "name": name,
            "run_type": "CHAIN",
            "status": "SUCCESS" if end_time else "PENDING",
            "start_time": "2026-01-02T00:00:00Z",
            "end_time": end_time,
        }
        return observations_to_span_dicts(
            runs_to_records([run]),
            credential=credential,
            conventions=LANGSMITH,
        )

    created = connector_sync._upsert_spans(
        credential.project, _spans(end_time=None), credential=credential
    )
    assert created == 1
    assert Span.objects.get(project=credential.project).name == "handler"
    assert Span.objects.get(project=credential.project).attributes["connector.version"] == "0"

    again = connector_sync._upsert_spans(
        credential.project,
        _spans(end_time="2026-01-02T00:00:01Z", name="handler-done"),
        credential=credential,
    )

    span = Span.objects.get(project=credential.project)
    assert again == 0
    assert span.name == "handler-done"
    assert int(span.attributes["connector.version"]) > 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
