"""A trace straddling a fetch window must be re-fetched whole before ingest.

Langfuse ingestion is async and batched, so a window can hold a child whose
parent starts outside it. Span ids are stable and the writer never revisits
them, so ingesting the half tree would root the orphan permanently.
"""

from datetime import UTC, datetime

from overbae.services.connectors.langfuse.client import LangFuseClient
from overbae.services.connectors.windows import TimeWindow


def _row(oid, parent=None, *, root=False, minute=0):
    return {
        "id": oid,
        "traceId": "t1",
        "parentObservationId": parent,
        "type": "SPAN",
        "name": oid,
        "startTime": f"2026-01-01T00:{minute:02d}:00Z",
        "isRootObservation": root,
    }


def _window():
    return TimeWindow(
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 2, tzinfo=UTC),
    )


def _client(monkeypatch, window_rows, trace_rows):
    calls = []

    def fake_get(self, path, **params):
        calls.append(params)
        rows = trace_rows if params.get("traceId") else window_rows
        return {"data": rows, "meta": {}}

    monkeypatch.setattr(LangFuseClient, "_get", fake_get)
    client = LangFuseClient("pk", "sk")
    client._api_version = "v2"
    return client, calls


def test_orphaned_child_triggers_a_whole_trace_refetch(monkeypatch):
    whole = [_row("root", root=True), _row("child", parent="root", minute=5)]
    client, calls = _client(monkeypatch, [_row("child", parent="root", minute=5)], whole)

    (group,) = list(client.iter_ingest_units(windows=[_window()]))

    assert sorted(o.id for o in group) == ["child", "root"]
    assert [c.get("traceId") for c in calls] == [None, "t1"]


def test_group_without_a_root_is_refetched(monkeypatch):
    """Every row has a resolvable parent, but the root itself is missing."""
    whole = [_row("root", root=True), _row("a", parent="root"), _row("b", parent="a")]
    client, calls = _client(monkeypatch, [_row("b", parent="a"), _row("a", parent="root")], whole)

    (group,) = list(client.iter_ingest_units(windows=[_window()]))

    assert len(group) == 3
    assert calls[-1]["traceId"] == "t1"


def test_complete_trace_is_not_refetched(monkeypatch):
    rows = [_row("root", root=True), _row("child", parent="root")]
    client, calls = _client(monkeypatch, rows, rows)

    (group,) = list(client.iter_ingest_units(windows=[_window()]))

    assert len(group) == 2
    assert all(c.get("traceId") is None for c in calls)  # no extra request


def test_paging_stops_when_the_cursor_repeats(monkeypatch):
    calls = []

    def fake_get(self, path, **params):
        calls.append(params)
        return {"data": [_row("root", root=True)], "meta": {"cursor": "stuck"}}

    monkeypatch.setattr(LangFuseClient, "_get", fake_get)
    client = LangFuseClient("pk", "sk")
    client._api_version = "v2"

    list(client.iter_ingest_units(windows=[_window()]))

    assert len(calls) <= 3  # would page forever without the guard


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
