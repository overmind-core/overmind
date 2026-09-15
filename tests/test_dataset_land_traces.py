"""Trace landing: one row per trace with the traces-table facts, the wire
transcript, the delivered output and the trace's score; two queries per chunk."""

from __future__ import annotations

import json
import uuid

import pandas as pd
import pytest

from overbae.models import Capability, Conversation, Dataset, Project, Span, TaskExecution
from overbae.services.datasets import land, paths, selection, store

pytestmark = pytest.mark.django_db

NS = 1_700_000_000_000_000_000
COLUMNS = [spec["name"] for spec in land.TRACE_MANIFEST]


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _span(project, trace_id, *, parent=None, span_type="llm_call", attrs=None, start=NS, **fields):
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=trace_id,
        parent_span_id=parent,
        project=project,
        span_type=span_type,
        name=fields.pop("name", span_type),
        start_time_ns=start,
        end_time_ns=start + 1_000_000_000,
        duration_ns=1_000_000_000,
        attributes=attrs or {},
        **fields,
    )


def _messages(text):
    return json.dumps([{"role": "user", "parts": [{"type": "text", "content": "q"}]}]), json.dumps(
        [{"role": "assistant", "parts": [{"type": "text", "content": text}]}]
    )


def _chat_trace(project, *, text="a", capability=None, conversation=None, error=False):
    trace = uuid.uuid4().hex
    root = _span(
        project,
        trace,
        span_type="entry_point",
        attrs={"overmind.span.type": "entry_point"},
        capability=capability,
        conversation=conversation,
        status_code=2 if error else 0,
        status_message="boom" if error else "",
    )
    inn, out = _messages(text)
    _span(
        project,
        trace,
        parent=root.span_id,
        start=NS + 10,
        attrs={
            "gen_ai.input.messages": inn,
            "gen_ai.output.messages": out,
            "genai.prompt_tokens": 100,
            "genai.completion_tokens": 20,
            "genai.cost": 0.001,
            "genai.model": "openai/gpt-5-mini",
        },
        conversation=conversation,
    )
    return trace, root


def _land(project, spec):
    dataset = Dataset.objects.create(project=project, name="t")
    land.land_traces(dataset, spec)
    dataset.refresh_from_db()
    return dataset, store.read_frame(paths.cell_path(dataset.id, dataset.source.id))


def test_one_row_per_trace_in_selection_order_with_the_fixed_columns():
    project = _project()
    capability = Capability.objects.create(project=project, name="Concierge", slug="concierge")
    first, _ = _chat_trace(project, text="one", capability=capability)
    second, _ = _chat_trace(project, text="two", capability=capability)
    dataset, df = _land(project, {"trace_ids": [second, first, second]})
    assert list(df.columns) == COLUMNS
    assert df["trace_id"].tolist() == [second, first]
    row = df.iloc[0]
    assert (row["capability"], row["capability_id"]) == ("Concierge", str(capability.id))
    assert row["status"] == "ok" and pd.isna(row["error"])
    assert (row["prompt_tokens"], row["completion_tokens"]) == (100, 20)
    assert row["cost_usd"] == pytest.approx(0.001)
    assert row["model"] == "openai/gpt-5-mini"
    assert row["duration_ms"] == 1000
    assert [m["role"] for m in row["messages"]] == ["user", "assistant"]
    assert row["messages"][-1]["content"] == "two"
    assert pd.isna(row["score"])
    assert dataset.capability_id == capability.id
    assert dataset.source_spec["trace_ids"] == [second, first]
    assert "grain" not in dataset.source_spec


def test_usage_sums_every_span_under_the_attribute_names_as_sent():
    project = _project()
    trace, root = _chat_trace(project)
    inn, out = _messages("b")
    _span(
        project,
        trace,
        parent=root.span_id,
        start=NS + 20,
        attrs={
            "gen_ai.input.messages": inn,
            "gen_ai.output.messages": out,
            "gen_ai.usage.input_tokens": 50,
            "gen_ai.usage.output_tokens": 10,
            "gen_ai.usage.cost": 0.0005,
            "gen_ai.request.model": "openai/gpt-5",
        },
    )
    _, df = _land(project, {"trace_ids": [trace]})
    row = df.iloc[0]
    assert (row["prompt_tokens"], row["completion_tokens"]) == (150, 30)
    assert row["cost_usd"] == pytest.approx(0.0015)
    assert row["model"] == "openai/gpt-5"


def test_delivered_output_wins_over_the_root_output():
    project = _project()
    trace, root = _chat_trace(project, text="draft")
    _span(
        project,
        trace,
        parent=root.span_id,
        start=NS + 30,
        span_type="tool",
        attrs={"overmind.delivery": "true", "overmind.output.data": json.dumps({"reply": "final"})},
    )
    _, df = _land(project, {"trace_ids": [trace]})
    assert df.iloc[0]["output"] == {"reply": "final"}


def test_score_is_the_last_scored_task_execution_and_session_id_is_the_wire_id():
    project = _project()
    conversation = Conversation.objects.create(project=project, external_id="conv-42")
    trace, root = _chat_trace(project, conversation=conversation)
    for offset, score in ((0, 0.2), (60, 0.9), (120, None)):
        TaskExecution.objects.create(
            project=project,
            trace_id=trace,
            unit_span_id=uuid.uuid4().hex[:16],
            started_at=land._ns_to_dt(NS + offset * 10**9),
            success_score=score,
        )
    _, df = _land(project, {"trace_ids": [trace]})
    row = df.iloc[0]
    assert row["score"] == pytest.approx(0.9)
    assert row["conversation_id"] == "conv-42"


def test_a_failed_trace_carries_its_status_and_error():
    project = _project()
    trace, _ = _chat_trace(project, error=True)
    _, df = _land(project, {"trace_ids": [trace]})
    assert (df.iloc[0]["status"], df.iloc[0]["error"]) == ("error", "boom")


def test_an_interrupted_trace_without_a_root_lands_from_its_longest_span():
    project = _project()
    trace = uuid.uuid4().hex
    orphan = _span(
        project,
        trace,
        parent=uuid.uuid4().hex[:16],
        span_type="entry_point",
        attrs={"overmind.span.type": "entry_point"},
    )
    inn, out = _messages("partial")
    _span(
        project,
        trace,
        parent=orphan.span_id,
        start=NS + 10,
        attrs={"gen_ai.input.messages": inn, "gen_ai.output.messages": out},
    )
    spec = {"filters": {"project": str(project.id)}, "ordering": "-start_time_ns"}
    assert list(selection.TraceSource.parse(spec).iter_trace_ids(project.id)) == [trace]
    _, df = _land(project, spec)
    assert df["trace_id"].tolist() == [trace]
    assert df.iloc[0]["messages"][-1]["content"] == "partial"


def test_a_chunk_of_traces_costs_two_queries(django_assert_num_queries):
    project = _project()
    ids = [_chat_trace(project)[0] for _ in range(5)]
    with django_assert_num_queries(2):
        rows = list(land.iter_trace_rows(project.id, ids))
    assert len(rows) == 5


def test_unknown_and_empty_traces_are_skipped_and_nothing_matching_is_an_error():
    project = _project()
    trace, _ = _chat_trace(project)
    _, df = _land(project, {"trace_ids": [trace, uuid.uuid4().hex]})
    assert len(df) == 1
    with pytest.raises(land.LandError):
        _land(project, {"trace_ids": [uuid.uuid4().hex]})
