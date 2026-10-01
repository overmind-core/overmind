from __future__ import annotations

import pytest
from factories import make_connector

from overbae.models import ConnectorCredential, Span
from overbae.tasks import connector_sync

pytestmark = pytest.mark.django_db


@pytest.fixture
def scored(monkeypatch) -> list[str]:
    queued: list[str] = []
    monkeypatch.setattr(
        "overbae.tasks.trace_scoring.score_trace.delay", lambda **kw: queued.append(kw["trace_id"])
    )
    return queued


def _live(connector_type: str, source: str = "", **fields) -> ConnectorCredential:
    credential = make_connector(connector_type, source_project_id=source, **fields)
    ConnectorCredential.objects.filter(pk=credential.pk).update(
        sync_cursor={"mode": "live"}, sync_status=ConnectorCredential.SyncStatus.LIVE
    )
    return credential


def _poll(credential) -> None:
    ConnectorCredential.objects.filter(pk=credential.pk).update(next_poll_at=None)
    connector_sync.sync_connector_chunk(str(credential.pk))


def _bt_row(*, xact_id, name="handler", scores=None):
    return {
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


@pytest.fixture
def braintrust(scripted, slept):
    api = scripted("https://api.braintrust.dev")
    return api, _live("braintrust", "bt-project", name="BT")


def _btql(api, *rows):
    api.reply(json_body={"data": list(rows)})


def test_a_newer_row_version_overwrites_the_stored_span_without_rescoring(braintrust, scored):
    api, credential = braintrust
    _btql(api, _bt_row(xact_id=1000))
    _btql(api, _bt_row(xact_id=2000, scores={"quality": 0.9}, name="handler-reviewed"))

    _poll(credential)
    _poll(credential)

    span = Span.objects.get(project=credential.project)
    assert span.name == "handler-reviewed"
    assert span.attributes["braintrust.scores"] == {"quality": 0.9}
    assert span.attributes["connector.version"] == "2000"
    assert scored == [span.trace_id]


def test_a_same_or_older_row_version_leaves_the_span_alone(braintrust, scored):
    api, credential = braintrust
    _btql(api, _bt_row(xact_id=2000))
    _btql(
        api,
        _bt_row(xact_id=2000, name="same-version-different-name"),
        _bt_row(xact_id=1, name="older"),
    )

    _poll(credential)
    _poll(credential)

    assert Span.objects.get(project=credential.project).name == "handler"


def test_an_overwrite_picks_up_a_renamed_credential(braintrust, scored):
    api, credential = braintrust
    _btql(api, _bt_row(xact_id=1000))
    _btql(api, _bt_row(xact_id=2000))
    _poll(credential)
    assert Span.objects.get(project=credential.project).service_name == "braintrust/BT"

    ConnectorCredential.objects.filter(pk=credential.pk).update(name="Braintrust prod")
    _poll(credential)

    assert Span.objects.get(project=credential.project).service_name == "braintrust/Braintrust prod"


def test_a_pending_langsmith_run_is_replaced_once_it_completes(scripted, slept, scored):
    api = scripted("https://api.smith.langchain.com")
    credential = _live("langsmith", "11111111-1111-1111-1111-111111111111")

    def run(end_time, name):
        return {
            "id": "22222222-2222-2222-2222-222222222222",
            "trace_id": "33333333-3333-3333-3333-333333333333",
            "parent_run_ids": [],
            "is_root": True,
            "name": name,
            "run_type": "CHAIN",
            "status": "SUCCESS" if end_time else "PENDING",
            "start_time": "2026-01-02T00:00:00Z",
            "end_time": end_time,
        }

    api.reply(json_body={"runs": [run(None, "handler")]})
    api.reply(json_body={"runs": [run("2026-01-02T00:00:01Z", "handler-done")]})

    _poll(credential)
    pending = Span.objects.get(project=credential.project)
    assert pending.attributes["connector.version"] == "0"
    _poll(credential)

    span = Span.objects.get(project=credential.project)
    assert span.name == "handler-done"
    assert int(span.attributes["connector.version"]) > 0
    assert len(scored) == 1


def test_a_vendor_without_a_row_version_stays_insert_only(scripted, slept, scored):
    api = scripted("https://cloud.langfuse.com")
    credential = _live("langfuse", api_secret="sk")

    def observation(name):
        return {
            "id": "obs-1",
            "traceId": "trace-1",
            "type": "SPAN",
            "name": name,
            "startTime": "2026-01-02T00:00:00Z",
            "isRootObservation": True,
        }

    for name in ("first", "first", "second", "second"):
        api.reply(json_body={"data": [observation(name)], "meta": {}})

    _poll(credential)
    _poll(credential)

    assert Span.objects.get(project=credential.project).name == "first"
