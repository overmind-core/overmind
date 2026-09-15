"""Usage lives on child LLM spans, not the root the ``/api/traces/`` list renders,
so trace totals must sum every span sharing the ``trace_id``."""

from __future__ import annotations

import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.api.serializers import SpanSerializer, trace_usage_totals
from overbae.models import Project, ProjectMembership, Span, User

pytestmark = pytest.mark.django_db

TRACES_URL = "/api/traces/"


def _client_and_project() -> tuple[APIClient, Project]:
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")
    return client, project


def _span(project, *, trace_id, parent=None, attributes=None, status_code=0, scope="") -> Span:
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent,
        project=project,
        status_code=status_code,
        scope_name=scope,
        attributes=attributes or {},
    )


def _trace_with_children(project) -> str:
    """A root span carrying NO usage + two child LLM spans that do."""
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, attributes={"genai.model": "gpt-5-nano"})
    _span(
        project,
        trace_id=trace_id,
        parent="a" * 16,
        attributes={"genai.prompt_tokens": 30, "genai.completion_tokens": 12, "genai.cost": 0.01},
    )
    _span(
        project,
        trace_id=trace_id,
        parent="a" * 16,
        attributes={"genai.total_tokens": 8, "genai.cost": 0.005},
    )
    return trace_id


def test_helper_sums_usage_across_all_spans_of_trace():
    _, project = _client_and_project()
    trace_id = _trace_with_children(project)

    totals = trace_usage_totals([project.id], [trace_id])
    assert totals[trace_id]["total_tokens"] == 50  # (30 + 12) + 8
    assert totals[trace_id]["total_cost"] == 0.015  # 0.01 + 0.005


def test_helper_counts_nested_double_instrumented_call_once():
    """A framework instrumentor's span wrapping a provider instrumentor's span
    reports the same call twice; only the leaf counts."""
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    root = _span(project, trace_id=trace_id)
    wrapper = _span(
        project,
        trace_id=trace_id,
        parent=root.span_id,
        attributes={"llm.usage.total_tokens": 930},
    )
    _span(
        project,
        trace_id=trace_id,
        parent=wrapper.span_id,
        attributes={"genai.usage.total_tokens": 930, "genai.cost": 0.02, "genai.model": "gpt-5.4"},
    )
    totals = trace_usage_totals([project.id], [trace_id])[trace_id]
    assert totals["total_tokens"] == 930
    assert totals["total_cost"] == 0.02


def test_helper_counts_cross_scope_twin_spans_once():
    """A framework instrumentor and a provider instrumentor keep separate
    context chains, so the same call lands twice under DIFFERENT parents:
    same trace, different scopes, overlapping windows, equal totals. The
    richer row (cost, model) wins."""
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    root = _span(project, trace_id=trace_id)
    Span.objects.filter(span_id=root.span_id).update(start_time_ns=0, end_time_ns=10_000)
    chain = _span(project, trace_id=trace_id, parent=root.span_id, scope="openinference.langchain")
    Span.objects.filter(span_id=chain.span_id).update(start_time_ns=500, end_time_ns=6_000)
    wrapper = _span(
        project,
        trace_id=trace_id,
        parent=chain.span_id,
        scope="openinference.langchain",
        attributes={"llm.usage.total_tokens": 930},
    )
    Span.objects.filter(span_id=wrapper.span_id).update(start_time_ns=1_000, end_time_ns=5_000)
    inner = _span(
        project,
        trace_id=trace_id,
        parent=root.span_id,
        scope="opentelemetry.instrumentation.openai.v1",
        attributes={"genai.usage.total_tokens": 930, "genai.cost": 0.02, "genai.model": "gpt-5.4"},
    )
    Span.objects.filter(span_id=inner.span_id).update(start_time_ns=1_200, end_time_ns=4_800)
    totals = trace_usage_totals([project.id], [trace_id])[trace_id]
    assert totals["total_tokens"] == 930
    assert totals["total_cost"] == 0.02
    assert totals["model"] == "gpt-5.4"


def test_helper_counts_sequential_identical_calls_separately():
    """Two identical calls that do NOT overlap in time are two real calls."""
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    root = _span(project, trace_id=trace_id)
    first = _span(
        project,
        trace_id=trace_id,
        parent=root.span_id,
        scope="opentelemetry.instrumentation.openai.v1",
        attributes={"genai.usage.total_tokens": 40},
    )
    Span.objects.filter(span_id=first.span_id).update(start_time_ns=0, end_time_ns=100)
    second = _span(
        project,
        trace_id=trace_id,
        parent=root.span_id,
        scope="openinference.langchain",
        attributes={"genai.usage.total_tokens": 40},
    )
    Span.objects.filter(span_id=second.span_id).update(start_time_ns=200, end_time_ns=300)
    totals = trace_usage_totals([project.id], [trace_id])[trace_id]
    assert totals["total_tokens"] == 80


def test_helper_counts_parallel_identical_same_scope_calls_separately():
    """Two parallel sub-queries from ONE instrumentor with equal totals are two
    real calls — same scope never twin-merges, whatever the overlap."""
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    root = _span(project, trace_id=trace_id)
    for start, end in ((0, 1_000), (100, 900)):
        span = _span(
            project,
            trace_id=trace_id,
            parent=root.span_id,
            scope="opentelemetry.instrumentation.openai.v1",
            attributes={"genai.usage.total_tokens": 500},
        )
        Span.objects.filter(span_id=span.span_id).update(start_time_ns=start, end_time_ns=end)
    totals = trace_usage_totals([project.id], [trace_id])[trace_id]
    assert totals["total_tokens"] == 1_000


def test_helper_never_twin_merges_rows_without_a_scope_name():
    """No scope name means no instrumentor identity — overlap plus an equal
    total is not evidence enough to drop a row."""
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    root = _span(project, trace_id=trace_id)
    for start, end in ((0, 1_000), (100, 900)):
        span = _span(
            project,
            trace_id=trace_id,
            parent=root.span_id,
            attributes={"genai.usage.total_tokens": 500},
        )
        Span.objects.filter(span_id=span.span_id).update(start_time_ns=start, end_time_ns=end)
    totals = trace_usage_totals([project.id], [trace_id])[trace_id]
    assert totals["total_tokens"] == 1_000


def test_helper_returns_none_when_no_span_reports_usage():
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, attributes={"genai.model": "gpt-5-nano"})

    totals = trace_usage_totals([project.id], [trace_id])
    assert totals[trace_id] == {
        "total_tokens": None,
        "total_cost": None,
        "cache_read_tokens": None,
        "model": "gpt-5-nano",
    }


@pytest.mark.parametrize(
    "attr_key",
    [
        "genai.model",
        "gen_ai.request.model",
        "gen_ai.response.model",
        "genai.response.model",
        "llm.model",
        "model",
    ],
)
def test_helper_surfaces_model_from_each_attribute_key(attr_key):
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id)
    _span(project, trace_id=trace_id, parent="a" * 16, attributes={attr_key: "claude-sonnet-4-5"})

    totals = trace_usage_totals([project.id], [trace_id])
    assert totals[trace_id]["model"] == "claude-sonnet-4-5"


def test_helper_model_none_when_absent():
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, attributes={"genai.total_tokens": 10})

    totals = trace_usage_totals([project.id], [trace_id])
    assert totals[trace_id]["model"] is None


def test_list_row_reports_trace_model():
    client, project = _client_and_project()
    trace_id = _trace_with_children(project)

    res = client.get(TRACES_URL)
    assert res.status_code == 200
    row = next(r for r in res.json()["results"] if r["trace_id"] == trace_id)
    assert row["model"] == "gpt-5-nano"


def test_list_row_reports_trace_level_totals():
    client, project = _client_and_project()
    trace_id = _trace_with_children(project)

    res = client.get(TRACES_URL)
    assert res.status_code == 200
    rows = res.json()["results"]
    row = next(r for r in rows if r["trace_id"] == trace_id)
    assert row["total_tokens"] == 50
    assert row["total_cost"] == 0.015


def test_detail_reports_the_same_totals_as_the_list():
    """The trace header reads ``usage`` off the detail payload — the client
    never re-derives totals from spans."""
    client, project = _client_and_project()
    trace_id = _trace_with_children(project)

    res = client.get(f"{TRACES_URL}{trace_id}/")
    assert res.status_code == 200
    usage = res.json()["usage"]
    assert usage["total_tokens"] == 50
    assert usage["total_cost"] == 0.015
    assert usage["model"] == "gpt-5-nano"
    assert usage["cache_read_tokens"] is None


def test_list_orders_by_aggregated_total_tokens():
    client, project = _client_and_project()
    low = uuid.uuid4().hex[:32]
    high = uuid.uuid4().hex[:32]
    _span(project, trace_id=low)
    _span(
        project,
        trace_id=low,
        parent="a" * 16,
        attributes={"genai.total_tokens": 10},
    )
    _span(project, trace_id=high)
    _span(
        project,
        trace_id=high,
        parent="a" * 16,
        attributes={"genai.total_tokens": 100},
    )

    asc = client.get(TRACES_URL, {"ordering": "total_tokens", "project": project.id})
    assert asc.status_code == 200
    asc_ids = [r["trace_id"] for r in asc.json()["results"] if r["trace_id"] in {low, high}]
    assert asc_ids == [low, high]

    desc = client.get(TRACES_URL, {"ordering": "-total_tokens", "project": project.id})
    assert desc.status_code == 200
    desc_ids = [r["trace_id"] for r in desc.json()["results"] if r["trace_id"] in {low, high}]
    assert desc_ids == [high, low]


def test_list_orders_by_model_and_execution_score():
    """``-trace_scores`` sorts on the Task Execution Score, not the chip count:
    a trace scored by many evaluators can still rank below a better one."""
    client, project = _client_and_project()
    a = uuid.uuid4().hex[:32]
    b = uuid.uuid4().hex[:32]
    root_a = _span(project, trace_id=a, attributes={"genai.model": "aaa-model"})
    root_b = _span(project, trace_id=b, attributes={"genai.model": "zzz-model"})
    Span.objects.filter(pk=root_a.pk).update(
        feedback_score={
            "trace_scoring": {
                "eval_one": {"score": 0.2, "outcome": "scored"},
                "eval_two": {"score": 0.4, "outcome": "scored"},
                "_execution": {"score": 0.3, "evaluations": 2},
                "_scored_at": "2026-01-01T00:00:00Z",
            }
        }
    )
    Span.objects.filter(pk=root_b.pk).update(
        feedback_score={
            "trace_scoring": {
                "eval_one": {"score": 0.9, "outcome": "scored"},
                "_execution": {"score": 0.9, "evaluations": 1},
                "_scored_at": "2026-01-01T00:00:00Z",
            }
        }
    )

    by_model = client.get(TRACES_URL, {"ordering": "model", "project": project.id})
    assert by_model.status_code == 200
    model_ids = [r["trace_id"] for r in by_model.json()["results"] if r["trace_id"] in {a, b}]
    assert model_ids == [a, b]

    by_scores = client.get(TRACES_URL, {"ordering": "-trace_scores", "project": project.id})
    assert by_scores.status_code == 200
    score_ids = [r["trace_id"] for r in by_scores.json()["results"] if r["trace_id"] in {a, b}]
    assert score_ids == [b, a]


def test_model_filter_returns_only_traces_that_invoked_that_model():
    """The model id lives in child spans' ``attributes`` JSON, so a root that
    carries no model must still surface."""
    client, project = _client_and_project()
    hit = uuid.uuid4().hex[:32]
    miss = uuid.uuid4().hex[:32]
    _span(project, trace_id=hit)
    _span(project, trace_id=hit, parent="a" * 16, attributes={"genai.model": "ft-model-x"})
    _span(project, trace_id=miss, attributes={"genai.model": "some-other-model"})

    res = client.get(TRACES_URL, {"model": "ft-model-x", "project": project.id})
    assert res.status_code == 200
    ids = {r["trace_id"] for r in res.json()["results"]}
    assert hit in ids
    assert miss not in ids


def test_models_facet_lists_the_distinct_models_of_that_project_only():
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id)
    _span(project, trace_id=trace_id, parent="a" * 16, attributes={"genai.model": "gpt-5-nano"})
    _span(
        project,
        trace_id=trace_id,
        parent="a" * 16,
        attributes={"gen_ai.request.model": "claude-sonnet-4-5"},
    )
    # Same model twice — the facet is a distinct list, not a tally.
    _span(project, trace_id=uuid.uuid4().hex[:32], attributes={"llm.model": "gpt-5-nano"})
    other = Project.objects.create(name="Other", slug=f"o-{uuid.uuid4().hex[:8]}")
    _span(other, trace_id=uuid.uuid4().hex[:32], attributes={"genai.model": "leaked-model"})

    res = client.get(f"{TRACES_URL}models/", {"project": project.id})
    assert res.status_code == 200
    assert res.json() == ["claude-sonnet-4-5", "gpt-5-nano"]


def test_has_model_filter_matches_traces_with_evidence_of_an_llm_call():
    """A trace counts when any of its spans reported a model, whatever
    ``span_type`` the ingest heuristic stamped on it."""
    client, project = _client_and_project()
    hit = uuid.uuid4().hex[:32]
    miss = uuid.uuid4().hex[:32]
    _span(project, trace_id=hit, attributes={"overmind.span.type": "entry_point"})
    _span(project, trace_id=hit, parent="a" * 16, attributes={"genai.model": "gpt-5-nano"})
    _span(project, trace_id=miss, attributes={"tool.name": "search"})

    res = client.get(TRACES_URL, {"has_model": "true", "project": project.id})
    assert res.status_code == 200
    ids = {r["trace_id"] for r in res.json()["results"]}
    assert ids == {hit}

    res = client.get(TRACES_URL, {"has_model": "false", "project": project.id})
    assert res.status_code == 200
    assert {r["trace_id"] for r in res.json()["results"]} == {miss}


def test_has_model_matches_a_failed_llm_call_that_reported_no_model():
    """An auth rejection or connect timeout leaves a ``gen_ai.*`` span with no
    model and no tokens: ``?has_model`` counts it, ``?model=`` must not."""
    client, project = _client_and_project()
    failed = uuid.uuid4().hex[:32]
    quiet = uuid.uuid4().hex[:32]
    _span(project, trace_id=failed, attributes={"overmind.span.type": "entry_point"})
    _span(
        project,
        trace_id=failed,
        parent="a" * 16,
        status_code=2,
        attributes={"gen_ai.system": "openai", "error.type": "AuthenticationError"},
    )
    _span(project, trace_id=quiet, attributes={"tool.name": "search"})

    res = client.get(TRACES_URL, {"has_model": "true", "project": project.id})
    assert res.status_code == 200
    assert {r["trace_id"] for r in res.json()["results"]} == {failed}

    res = client.get(TRACES_URL, {"has_model": "false", "project": project.id})
    assert res.status_code == 200
    assert {r["trace_id"] for r in res.json()["results"]} == {quiet}

    res = client.get(TRACES_URL, {"model": "gpt-5-nano", "project": project.id})
    assert res.status_code == 200
    assert res.json()["results"] == []


@pytest.mark.parametrize(
    ("attributes", "is_match"),
    [
        ({"genai.model": "gpt-5-nano"}, True),
        ({"model": "gpt-5-nano"}, True),
        ({"genai.total_tokens": 42}, True),
        ({"genai.prompt_tokens": 30, "genai.completion_tokens": 12}, True),
        ({"gen_ai.system": "anthropic"}, True),
        ({"llm.request.type": "chat"}, True),
        # The semconv reuses ``gen_ai.operation.name`` for tool invocations, which
        # ingest types as ``tool_call`` — every other operation is a model call.
        ({"gen_ai.operation.name": "execute_tool"}, False),
        ({"gen_ai.operation.name": "EXECUTE_TOOL "}, False),
        ({"gen_ai.operation.name": "chat"}, True),
        ({"gen_ai.operation.name": "embeddings"}, True),
        ({}, False),
        ({"tool.name": "search"}, False),
        ({"overmind.span.type": "entry_point", "http.method": "POST"}, False),
    ],
)
def test_has_model_matches_any_evidence_of_a_genai_operation(attributes, is_match):
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id)
    _span(project, trace_id=trace_id, parent="a" * 16, attributes=attributes)

    res = client.get(TRACES_URL, {"has_model": "true", "project": project.id})
    assert res.status_code == 200
    matched = trace_id in {r["trace_id"] for r in res.json()["results"]}
    assert matched is is_match, attributes


def test_root_list_aggregates_when_all_spans_false_string():
    """``all_spans=false`` arrives as a truthy string, so the list view must parse
    it as a boolean rather than test raw truthiness."""
    client, project = _client_and_project()
    trace_id = _trace_with_children(project)

    res = client.get(TRACES_URL, {"all_spans": "false"})
    assert res.status_code == 200
    rows = res.json()["results"]
    row = next(r for r in rows if r["trace_id"] == trace_id)
    assert row["total_tokens"] == 50
    assert row["total_cost"] == 0.015


def test_helper_aggregates_cache_read_tokens_separately():
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, attributes={"genai.model": "gpt-5-nano"})
    _span(
        project,
        trace_id=trace_id,
        parent="a" * 16,
        attributes={"genai.total_tokens": 100, "genai.cache_read_tokens": 40},
    )
    _span(
        project,
        trace_id=trace_id,
        parent="a" * 16,
        attributes={"genai.total_tokens": 20, "genai.cache_read_tokens": 5},
    )

    totals = trace_usage_totals([project.id], [trace_id])
    assert totals[trace_id]["total_tokens"] == 120  # cache-read NOT folded in
    assert totals[trace_id]["cache_read_tokens"] == 45  # 40 + 5


def test_helper_cache_read_none_when_absent():
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, attributes={"genai.total_tokens": 10})

    totals = trace_usage_totals([project.id], [trace_id])
    assert totals[trace_id]["cache_read_tokens"] is None


def test_helper_reads_the_usage_projection_not_attributes():
    """Rollups read only the ``Span.usage`` slice ingest projects — a multi-MB
    ``attributes`` blob must never be detoasted by a list query."""
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(
        project,
        trace_id=trace_id,
        attributes={"genai.total_tokens": 7, "snapshot": "x" * 2_000},
    )
    with CaptureQueriesContext(connection) as ctx:
        totals = trace_usage_totals([project.id], [trace_id])
    assert totals[trace_id]["total_tokens"] == 7
    sql = " ".join(q["sql"] for q in ctx.captured_queries)
    assert '"usage"' in sql
    assert '"attributes"' not in sql


def test_list_row_reports_cache_read_tokens():
    client, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    _span(project, trace_id=trace_id, attributes={"genai.model": "gpt-5-nano"})
    _span(
        project,
        trace_id=trace_id,
        parent="a" * 16,
        attributes={"genai.total_tokens": 100, "genai.cache_read_tokens": 40},
    )

    res = client.get(TRACES_URL)
    assert res.status_code == 200
    row = next(r for r in res.json()["results"] if r["trace_id"] == trace_id)
    assert row["total_tokens"] == 100
    assert row["cache_read_tokens"] == 40


def test_span_serializer_surfaces_new_genai_and_retrieval_keys():
    _, project = _client_and_project()
    trace_id = uuid.uuid4().hex[:32]
    attributes = {
        "genai.model": "gpt-5-nano",
        "genai.cache_read_tokens": 40,
        "genai.request.temperature": 0.7,
        "genai.request.max_tokens": 512,
        "genai.request.top_p": 0.95,
        "genai.response.finish_reason": "stop",
        "genai.response.message_chars": 128,
        "genai.streaming": True,
        "genai.time_to_first_token_seconds": 0.42,
        "overmind.retrieval.query_chars": 64,
        "overmind.retrieval.result_count": 5,
    }
    span = _span(project, trace_id=trace_id, attributes=attributes)

    serialized = SpanSerializer(span).data["attributes"]
    for key, value in attributes.items():
        assert serialized[key] == value


def test_usage_filters_match_the_trace_wide_totals_the_row_shows():
    client, project = _client_and_project()
    trace_id = _trace_with_children(project)  # 50 tokens / $0.015, all on children

    for params, should_match in (
        ({"total_tokens__gte": 20}, True),
        ({"total_tokens__lte": 20}, False),
        ({"total_cost__gte": 0.01}, True),
        ({"total_cost__lte": 0.01}, False),
    ):
        res = client.get(TRACES_URL, {**params, "all_spans": "false", "project": project.id})
        assert res.status_code == 200
        matched = trace_id in {r["trace_id"] for r in res.json()["results"]}
        assert matched is should_match, params


def test_usage_filter_is_per_span_in_the_all_spans_view():
    client, project = _client_and_project()
    _trace_with_children(project)  # children carry 42 and 8 tokens

    res = client.get(
        TRACES_URL, {"all_spans": "true", "total_tokens__gte": 40, "project": project.id}
    )
    assert res.status_code == 200
    assert len(res.json()["results"]) == 1


def test_no_n_plus_one_query_growth_with_more_traces():
    client, project = _client_and_project()
    _trace_with_children(project)

    with CaptureQueriesContext(connection) as few:
        assert client.get(TRACES_URL).status_code == 200
    few_count = len(few.captured_queries)

    for _ in range(5):
        _trace_with_children(project)

    with CaptureQueriesContext(connection) as many:
        assert client.get(TRACES_URL).status_code == 200
    many_count = len(many.captured_queries)

    # Per-page totals are one extra query, so the count must not grow with rows.
    assert many_count == few_count
