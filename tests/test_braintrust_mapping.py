"""Braintrust row → record → span mapping, and reuse of the shared capability layer."""

from types import SimpleNamespace

import pytest

from overbae.services.connectors.braintrust.mapping import (
    BRAINTRUST,
    group_rows_by_trace,
    rows_to_records,
)
from overbae.services.connectors.capabilities import assign_capability_keys
from overbae.services.connectors.mapping import observations_to_span_dicts as _to_span_dicts
from overbae.services.connectors.profiling import profile_capability_candidates
from overbae.services.connectors.schema import (
    CONNECTOR_SOURCE_ATTR,
    CONNECTOR_VERSION_ATTR,
)
from overbae.services.connectors.spans import span_id_for

# 2026-01-02T00:00:00Z and one second later, as Braintrust logs them.
_START = 1767312000.0


def observations_to_span_dicts(observations, **kwargs):
    """Every case here is a Braintrust trace, so bind its conventions once."""
    return _to_span_dicts(observations, conventions=BRAINTRUST, **kwargs)


def _row(span_id, *, row_id=None, parents=(), is_root=False, name="span", **extra):
    row = {
        "id": row_id or f"id-{span_id}",
        "span_id": span_id,
        "span_parents": list(parents),
        "root_span_id": "root-span",
        "is_root": is_root,
        "created": "2026-01-02T00:00:00+00:00",
        "_xact_id": 1000,
        "span_attributes": {"name": name, "type": "llm"},
        "metrics": {"start": _START, "end": _START + 1},
    }
    row.update(extra)
    return row


def _cred():
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        name="demo",
        project=SimpleNamespace(id="p", slug="p"),
        capability_mapping={},
    )


def _trace_rows():
    return [
        _row("s-root", row_id="id-root", is_root=True, name="handler"),
        _row("s-child", row_id="id-child", parents=["s-root"], name="llm"),
    ]


def test_the_other_metrics_braintrust_reports_reach_the_span():
    rows = _trace_rows()
    rows[1]["metrics"] |= {
        "time_to_first_token": 0.42,
        "prompt_cached_tokens": 1024,
        "completion_audio_tokens": 0,  # zero-filled on every LLM span
        "estimated_cost": 0.003,
    }
    rows[1]["metadata"] = {"model": "gpt-5", "temperature": 0.2}

    spans = observations_to_span_dicts(rows_to_records(rows), credential=_cred())
    child_id = span_id_for(str(_cred().id), "id-child")
    attrs = next(s for s in spans if s["span_id"] == child_id)["attributes"]

    assert attrs["braintrust.metrics.time_to_first_token"] == 0.42
    assert attrs["braintrust.metrics.prompt_cached_tokens"] == 1024
    assert "braintrust.metrics.completion_audio_tokens" not in attrs
    # Cost and model already have a home, so they are not stamped twice.
    assert "braintrust.metrics.estimated_cost" not in attrs
    assert "braintrust.metadata.model" not in attrs
    assert attrs["braintrust.metadata.temperature"] == 0.2


def test_values_longer_than_their_column_are_trimmed_rather_than_failing_the_page():
    rows = _trace_rows()
    rows[1]["span_attributes"] = {"name": "x" * 400, "type": "llm"}
    credential = _cred()
    credential.name = "n" * 400

    spans = observations_to_span_dicts(rows_to_records(rows), credential=credential)
    child = next(s for s in spans if s["name"].startswith("x"))

    assert len(child["name"]) == 255
    assert child["name"].endswith("…")
    assert len(child["operation"]) == 400  # operation holds 512, so it is untouched
    assert all(len(s["service_name"]) == 255 for s in spans)


def test_online_scoring_output_is_not_imported_as_application_work():
    rows = _trace_rows() + [
        _row(
            "s-score",
            row_id="id-score",
            parents=["s-child"],
            span_attributes={"name": "Factuality", "type": "score"},
        ),
        # The judge's own model call hangs off the score span.
        _row(
            "s-judge",
            row_id="id-judge",
            parents=["s-score"],
            span_attributes={"name": "judge", "type": "llm"},
        ),
    ]

    assert {r.id for r in rows_to_records(rows)} == {"id-root", "id-child"}


def test_a_trace_that_is_only_scorer_output_imports_nothing():
    rows = [
        _row(
            "s-score",
            row_id="id-score",
            is_root=True,
            span_attributes={"name": "Factuality", "type": "classifier"},
        )
    ]
    assert rows_to_records(rows) == []


def test_session_and_user_come_from_metadata_and_reach_every_span():
    rows = _trace_rows()
    # Braintrust's log viewer groups on metadata.conversation_id, and apps log it
    # on the root only — the child here deliberately carries neither key.
    rows[0]["metadata"] = {"conversation_id": "conv-7", "user_id": "u-1"}
    records = rows_to_records(rows)

    assert {r.session_id for r in records} == {"conv-7"}
    assert {r.user_id for r in records} == {"u-1"}

    spans = observations_to_span_dicts(records, credential=_cred())
    assert all(s["attributes"]["conversation.id"] == "conv-7" for s in spans)
    assert all(s["attributes"]["braintrust.user_id"] == "u-1" for s in spans)


def test_session_falls_back_through_the_spellings_braintrust_documents():
    for key in ("session_id", "thread_id"):
        rows = _trace_rows()
        rows[0]["metadata"] = {key: "s-1"}
        assert {r.session_id for r in rows_to_records(rows)} == {"s-1"}


def test_a_trace_with_no_session_metadata_stays_sessionless():
    records = rows_to_records(_trace_rows())
    assert {r.session_id for r in records} == {None}
    spans = observations_to_span_dicts(records, credential=_cred())
    assert not any("conversation.id" in s["attributes"] for s in spans)


def test_input_and_output_reach_the_span():
    rows = _trace_rows()
    rows[1] |= {"input": {"messages": [{"role": "user", "content": "hi"}]}, "output": "hello"}
    records = rows_to_records(rows)

    child = next(r for r in records if r.id == "id-child")
    assert child.input == {"messages": [{"role": "user", "content": "hi"}]}

    spans = observations_to_span_dicts(records, credential=_cred())
    child_span = next(s for s in spans if s["span_id"] == span_id_for(str(_cred().id), "id-child"))
    assert child_span["attributes"]["overmind.input.data"] == rows[1]["input"]
    assert child_span["attributes"]["overmind.output.data"] == "hello"


def test_function_spans_are_tool_calls_not_llm_calls():
    """Braintrust spells its types in lower case, so an upper-cased table must match."""
    rows = [
        _row("s-root", row_id="id-root", is_root=True, name="handler"),
        _row("s-tool", row_id="id-tool", parents=["s-root"], name="lookup"),
        _row("s-llm", row_id="id-llm", parents=["s-root"], name="answer"),
    ]
    rows[1]["span_attributes"] = {"name": "lookup", "type": "function"}
    spans = observations_to_span_dicts(rows_to_records(rows), credential=_cred())
    by_name = {s["name"]: s for s in spans}

    assert by_name["lookup"]["span_type"] == "tool_call"
    assert by_name["answer"]["span_type"] == "llm_call"


def test_parents_resolve_through_span_id_not_id():
    records = rows_to_records(_trace_rows())
    child = next(r for r in records if r.id == "id-child")

    # span_parents held "s-root", so the parent must come back as that row's id.
    assert child.parent_observation_id == "id-root"


def test_parents_resolve_when_otel_sets_span_id_equal_to_id():
    rows = [
        _row("id-root", row_id="id-root", is_root=True),
        _row("id-child", row_id="id-child", parents=["id-root"]),
    ]
    records = rows_to_records(rows)

    assert next(r.parent_observation_id for r in records if r.id == "id-child") == "id-root"


def test_multi_parent_takes_the_first_sorted_and_keeps_the_whole_array(caplog):
    rows = [
        _row("s-a", row_id="id-a", is_root=True),
        _row("s-b", row_id="id-b", parents=["s-a"]),
        _row("s-c", row_id="id-c", parents=["s-b", "s-a"]),
    ]
    with caplog.at_level("INFO"):
        records = rows_to_records(rows, credential_id="cred-1")

    fan_in = next(r for r in records if r.id == "id-c")
    assert fan_in.parent_observation_id == "id-a"  # sorted(["s-b", "s-a"])[0] == "s-a"
    assert fan_in.extra_attrs["span_parents"] == ["s-b", "s-a"]
    assert "has 2 parents" in caplog.text


def test_one_span_belongs_to_one_capability_only():
    rows = [
        _row("s-a", row_id="id-a", is_root=True, name="root"),
        _row("s-b", row_id="id-b", parents=["s-a"], name="capability_b"),
        _row("s-c", row_id="id-c", parents=["s-b", "s-a"], name="shared"),
    ]
    records = rows_to_records(rows)
    keys = assign_capability_keys(
        records, {"source": "observation_name", "names": ["capability_b", "root"]}
    )

    assert keys["id-c"] == "root"
    assert len([k for k in (keys["id-c"],) if k]) == 1


def test_float_unix_timings_become_iso_and_survive_into_span_ns():
    records = rows_to_records(_trace_rows())
    root = next(r for r in records if r.is_root_observation)

    assert root.start_time == "2026-01-02T00:00:00+00:00"
    assert root.end_time == "2026-01-02T00:00:01+00:00"

    spans = observations_to_span_dicts(records, credential=_cred())
    root_span = next(s for s in spans if s["parent_span_id"] is None)
    assert root_span["duration_ns"] == 1_000_000_000


def test_error_object_becomes_an_error_status():
    rows = _trace_rows()
    rows[1]["error"] = {"message": "boom", "code": 500}
    records = rows_to_records(rows)
    child = next(r for r in records if r.id == "id-child")

    assert child.level == "ERROR"
    assert "boom" in child.status_message

    spans = observations_to_span_dicts(records, credential=_cred())
    child_span = next(s for s in spans if s["span_id"] == span_id_for(str(_cred().id), "id-child"))
    assert child_span["status_code"] == 2


def test_cost_comes_from_estimated_cost_with_a_null_root():
    rows = _trace_rows()
    rows[0]["estimated_cost"] = None
    rows[1]["estimated_cost"] = 0.0042
    rows[1]["metrics"] |= {"prompt_tokens": 10, "completion_tokens": 5, "tokens": 15}
    records = rows_to_records(rows)

    assert next(r.total_cost for r in records if r.is_root_observation) is None
    assert next(r.total_cost for r in records if r.id == "id-child") == pytest.approx(0.0042)


def test_root_less_group_roots_at_the_earliest_span():
    rows = [
        _row("s-b", row_id="id-b", parents=["s-missing"], name="second"),
        _row("s-a", row_id="id-a", parents=["s-missing"], name="first"),
    ]
    rows[1]["metrics"] = {"start": _START - 5, "end": _START}
    records = rows_to_records(rows)

    root = [r for r in records if r.is_root_observation]
    assert [r.id for r in root] == ["id-a"]
    assert next(r.parent_observation_id for r in records if r.id == "id-b") == "id-a"


def test_records_carry_the_row_version_into_span_attributes():
    records = rows_to_records(_trace_rows())
    spans = observations_to_span_dicts(records, credential=_cred())

    assert all(s["attributes"][CONNECTOR_VERSION_ATTR] == "1000" for s in spans)


def test_spans_are_stamped_with_the_braintrust_source():
    records = rows_to_records(_trace_rows())
    spans = observations_to_span_dicts(records, credential=_cred())

    assert {s["attributes"][CONNECTOR_SOURCE_ATTR] for s in spans} == {"braintrust"}
    assert all(s["scope_name"] == "braintrust" for s in spans)
    assert all("braintrust.trace_id" in s["attributes"] for s in spans)


def test_observation_name_boundary_roots_its_own_trace():
    rows = [
        _row("s-root", row_id="id-root", is_root=True, name="handler"),
        _row("s-capability", row_id="id-capability", parents=["s-root"], name="researcher"),
        _row("s-leaf", row_id="id-leaf", parents=["s-capability"], name="llm"),
    ]
    records = rows_to_records(rows)
    spans = observations_to_span_dicts(
        records,
        credential=_cred(),
        mapping={"source": "observation_name", "names": ["researcher"]},
    )

    roots = [s for s in spans if s["parent_span_id"] is None]
    assert sorted(s["name"] for s in roots) == ["handler", "researcher"]
    # The nested capability keeps its subtree.
    capability_trace = next(s["trace_id"] for s in roots if s["name"] == "researcher")
    leaf = next(s for s in spans if s["name"] == "llm")
    assert leaf["trace_id"] == capability_trace


def test_group_rows_by_trace_keys_on_root_span_id():
    rows = _trace_rows() + [_row("s-other", row_id="id-other", root_span_id="other-root")]
    groups = group_rows_by_trace(rows)

    assert sorted(groups) == ["other-root", "root-span"]


def test_shared_profiler_ranks_braintrust_records_unchanged():
    """The payoff of the neutral record: no Braintrust-specific profiling code."""
    traces = []
    for i in range(4):
        rows = [
            _row("s-root", row_id=f"root-{i}", is_root=True, name="handler"),
            _row("s-capability", row_id=f"capability-{i}", parents=["s-root"], name="researcher"),
            _row("s-llm-1", row_id=f"llm-a-{i}", parents=["s-capability"], name="chat"),
            _row("s-llm-2", row_id=f"llm-b-{i}", parents=["s-capability"], name="chat"),
        ]
        traces.append(rows_to_records(rows))

    shapes = profile_capability_candidates(traces, BRAINTRUST)
    names = [s["name"] for s in shapes]

    assert names[0] in {"handler", "researcher"}
    # "chat" repeats twice per trace, so it is a loop step, not a boundary.
    assert names.index("chat") > names.index("researcher")
    assert next(s["parent_name"] for s in shapes if s["name"] == "researcher") == "handler"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
