"""The trace source boundary: unknown filters are refused, ids and filters never
mix, and a selection is counted before anything lands."""

from __future__ import annotations

import uuid

import pytest

from overbae.models import Project, Span
from overbae.services.datasets.selection import TraceSource, TraceSourceError, allowed_filters

pytestmark = pytest.mark.django_db

NS = 1_700_000_000_000_000_000


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _root(project, *, start=NS, error=False) -> str:
    trace = uuid.uuid4().hex
    Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace,
        project=project,
        span_type="entry_point",
        name="run",
        start_time_ns=start,
        end_time_ns=start + 10**9,
        duration_ns=10**9,
        status_code=2 if error else 1,
    )
    return trace


def test_parse_keeps_explicit_ids_in_order_and_deduped():
    source = TraceSource.parse({"trace_ids": [" a ", "b", "a", ""]})
    assert source.trace_ids == ("a", "b")
    assert source.spec() == {"trace_ids": ["a", "b"]}


def test_parse_refuses_unknown_filters_and_names_the_allowed_ones():
    with pytest.raises(TraceSourceError) as exc:
        TraceSource.parse({"filters": {"capability_name": "x", "has_error": "true"}})
    assert "capability_name" in str(exc.value)
    assert "has_error" in str(exc.value)
    assert "all_spans" not in allowed_filters()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"filters": {}},
        {"filters": {"has_error": ""}},
        {"trace_ids": ["a"], "filters": {"has_error": "true"}},
        {"trace_ids": ["a"], "search": "x"},
        {"filters": {"has_error": "true"}, "limit": 0},
        {"filters": {"has_error": "true"}, "limit": "many"},
        "abc",
    ],
)
def test_parse_refuses_empty_mixed_and_malformed_payloads(payload):
    with pytest.raises(TraceSourceError):
        TraceSource.parse(payload)


def test_parse_normalises_a_traces_list_selection():
    source = TraceSource.parse(
        {
            "filters": {"has_error": "true", "model": None, "project": ""},
            "search": " pay ",
            "ordering": "-start_time_ns",
            "exclude_trace_ids": ["x", "x"],
            "limit": "5",
        }
    )
    assert source.spec() == {
        "filters": {"has_error": "true"},
        "search": "pay",
        "ordering": "-start_time_ns",
        "exclude_trace_ids": ["x"],
        "limit": 5,
    }


def test_count_explicit_ids_counts_only_traces_that_exist():
    project = _project()
    real = _root(project)
    source = TraceSource.parse({"trace_ids": [real, uuid.uuid4().hex]})
    assert source.count(project.id) == 1
    assert list(source.iter_trace_ids(project.id)) == [real, source.trace_ids[1]]


def test_count_selection_applies_filters_exclusions_and_limit():
    project = _project()
    failed = [_root(project, start=NS + i, error=True) for i in range(3)]
    _root(project)
    source = TraceSource.parse({"filters": {"has_error": "true"}})
    assert source.count(project.id) == 3
    assert set(source.iter_trace_ids(project.id)) == set(failed)
    excluded = TraceSource.parse(
        {"filters": {"has_error": "true"}, "exclude_trace_ids": [failed[0]]}
    )
    assert excluded.count(project.id) == 2
    capped = TraceSource.parse({"filters": {"has_error": "true"}, "limit": 1})
    assert capped.count(project.id) == 1
    assert len(list(capped.iter_trace_ids(project.id))) == 1


def test_count_is_scoped_to_the_project():
    mine, theirs = _project(), _project()
    _root(theirs)
    source = TraceSource.parse({"filters": {"project": str(mine.id)}})
    assert source.count(mine.id) == 0
    with pytest.raises(TraceSourceError):
        TraceSource.parse({"filters": {"received_at__gte": "yesterday"}}).count(mine.id)
