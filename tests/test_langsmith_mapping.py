"""LangSmith run → record → span mapping, and reuse of the shared capability layer."""

from types import SimpleNamespace

import pytest

from overbae.services.connectors.langsmith.mapping import (
    LANGSMITH,
    runs_to_records,
)
from overbae.services.connectors.mapping import observations_to_span_dicts as _to_span_dicts
from overbae.services.connectors.profiling import profile_capability_candidates
from overbae.services.connectors.schema import (
    CONNECTOR_SOURCE_ATTR,
    CONNECTOR_VERSION_ATTR,
)
from overbae.services.connectors.spans import span_id_for

_ROOT = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa1"
_CHILD = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa2"
_TRACE = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa0"


def observations_to_span_dicts(observations, **kwargs):
    return _to_span_dicts(observations, conventions=LANGSMITH, **kwargs)


def _run(run_id, *, parent_ids=(), is_root=False, name="span", **extra):
    run = {
        "id": run_id,
        "trace_id": _TRACE,
        "parent_run_ids": list(parent_ids),
        "is_root": is_root,
        "name": name,
        "run_type": "LLM",
        "status": "SUCCESS",
        "start_time": "2026-01-02T00:00:00Z",
        "end_time": "2026-01-02T00:00:01Z",
    }
    run.update(extra)
    return run


def _cred():
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        name="demo",
        project=SimpleNamespace(id="p", slug="p"),
        capability_mapping={},
    )


def _trace_runs():
    return [
        _run(_ROOT, is_root=True, name="handler", run_type="CHAIN"),
        _run(_CHILD, parent_ids=[_ROOT], name="llm"),
    ]


def test_parent_is_the_last_ancestor_in_parent_run_ids():
    mid = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaa3"
    runs = [
        _run(_ROOT, is_root=True, name="handler", run_type="CHAIN"),
        _run(mid, parent_ids=[_ROOT], name="tool", run_type="TOOL"),
        _run(_CHILD, parent_ids=[_ROOT, mid], name="llm"),
    ]
    records = runs_to_records(runs)

    assert next(r.parent_observation_id for r in records if r.id == _CHILD) == mid
    assert next(r.parent_observation_id for r in records if r.id == mid) == _ROOT


def test_parent_falls_back_to_parent_run_id_when_the_ancestor_list_is_empty():
    runs = [
        _run(_ROOT, is_root=True, name="handler", run_type="CHAIN"),
        _run(_CHILD, parent_ids=[], is_root=False, name="llm", parent_run_id=_ROOT),
    ]
    records = runs_to_records(runs)

    assert next(r.parent_observation_id for r in records if r.id == _CHILD) == _ROOT


def test_parent_falls_back_to_the_penultimate_dotted_order_uuid():
    dotted = f"20260102T000000000000Z{_ROOT}.20260102T000001000000Z{_CHILD}"
    runs = [
        _run(_ROOT, is_root=True, name="handler", run_type="CHAIN"),
        _run(_CHILD, parent_ids=[], is_root=False, name="llm", dotted_order=dotted),
    ]
    records = runs_to_records(runs)

    assert next(r.parent_observation_id for r in records if r.id == _CHILD) == _ROOT


def test_naive_timestamps_are_read_as_utc():
    runs = _trace_runs()
    runs[0]["start_time"] = "2026-01-02T00:00:00"
    runs[0]["end_time"] = "2026-01-02T00:00:01"
    records = runs_to_records(runs)
    root = next(r for r in records if r.is_root_observation)

    assert root.start_time == "2026-01-02T00:00:00+00:00"
    assert root.end_time == "2026-01-02T00:00:01+00:00"

    spans = observations_to_span_dicts(records, credential=_cred())
    root_span = next(s for s in spans if s["parent_span_id"] is None)
    assert root_span["duration_ns"] == 1_000_000_000


def test_thread_id_becomes_the_conversation_and_reaches_every_span():
    runs = _trace_runs()
    runs[0]["thread_id"] = "thread-7"
    runs[0]["metadata"] = {"user_id": "u-1"}
    records = runs_to_records(runs)

    assert {r.session_id for r in records} == {"thread-7"}
    assert {r.user_id for r in records} == {"u-1"}

    spans = observations_to_span_dicts(records, credential=_cred())
    assert all(s["attributes"]["conversation.id"] == "thread-7" for s in spans)
    assert all(s["attributes"]["langsmith.user_id"] == "u-1" for s in spans)


def test_langsmith_session_id_is_not_a_conversation():
    runs = _trace_runs()
    project_uuid = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    runs[0]["session_id"] = project_uuid
    records = runs_to_records(runs)

    assert {r.session_id for r in records} == {None}
    spans = observations_to_span_dicts(records, credential=_cred())
    assert not any("conversation.id" in s["attributes"] for s in spans)
    assert project_uuid not in {s["attributes"].get("conversation.id") for s in spans}


def test_metadata_falls_back_to_extra_metadata():
    runs = _trace_runs()
    runs[0]["extra"] = {"metadata": {"thread_id": "from-extra", "temperature": 0.2}}
    records = runs_to_records(runs)

    assert {r.session_id for r in records} == {"from-extra"}
    spans = observations_to_span_dicts(records, credential=_cred())
    root = next(s for s in spans if s["parent_span_id"] is None)
    assert root["attributes"]["langsmith.metadata.temperature"] == 0.2


def test_model_comes_from_ls_model_name_then_invocation_params():
    runs = _trace_runs()
    runs[1]["metadata"] = {"ls_model_name": "gpt-5"}
    records = runs_to_records(runs)
    assert next(r.model for r in records if r.id == _CHILD) == "gpt-5"

    runs[1]["metadata"] = {}
    runs[1]["extra"] = {"invocation_params": {"model": "claude"}}
    assert next(r.model for r in runs_to_records(runs) if r.id == _CHILD) == "claude"


def test_tokens_and_cost_map_onto_usage_attributes():
    runs = _trace_runs()
    runs[1] |= {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "total_tokens": 15,
        "total_cost": 0.0042,
    }
    records = runs_to_records(runs)
    child = next(r for r in records if r.id == _CHILD)

    assert child.usage_details == {"input": 10, "output": 5, "total": 15}
    assert child.total_cost == pytest.approx(0.0042)


def test_error_status_becomes_an_error_level_and_otel_status():
    runs = _trace_runs()
    runs[1]["status"] = "ERROR"
    runs[1]["error"] = "boom"
    records = runs_to_records(runs)
    child = next(r for r in records if r.id == _CHILD)

    assert child.level == "ERROR"
    assert "boom" in child.status_message

    spans = observations_to_span_dicts(records, credential=_cred())
    child_span = next(s for s in spans if s["span_id"] == span_id_for(str(_cred().id), _CHILD))
    assert child_span["status_code"] == 2


def test_error_status_is_matched_without_regard_to_case():
    runs = _trace_runs()
    runs[1]["status"] = "error"
    records = runs_to_records(runs)
    assert next(r.level for r in records if r.id == _CHILD) == "ERROR"


def test_a_clipped_window_does_not_promote_orphans_until_it_is_complete():
    runs = [_run(_CHILD, parent_ids=[_ROOT], name="llm")]
    sliced = runs_to_records(runs, promote_orphans=False)

    assert sliced[0].is_root_observation is False
    assert sliced[0].parent_observation_id == _ROOT

    complete = runs_to_records(runs, promote_orphans=True)
    assert complete[0].is_root_observation is True
    assert complete[0].parent_observation_id is None


def test_pending_row_version_is_zero_and_completion_raises_it():
    pending = _run(
        _ROOT, is_root=True, name="handler", run_type="CHAIN", end_time=None, status="PENDING"
    )
    done = _run(_ROOT, is_root=True, name="handler", run_type="CHAIN")

    pending_v = runs_to_records([pending])[0].row_version
    done_v = runs_to_records([done])[0].row_version

    assert pending_v == "0"
    assert int(done_v) > 0
    assert int(done_v) > int(pending_v)

    spans = observations_to_span_dicts(runs_to_records([done]), credential=_cred())
    assert all(s["attributes"][CONNECTOR_VERSION_ATTR] == done_v for s in spans)


def test_row_version_ignores_an_unsupported_mutation_timestamp():
    first = _run(_ROOT, is_root=True, updated_at="2026-01-02T00:00:02Z")
    later = _run(_ROOT, is_root=True, updated_at="2026-01-03T00:00:00Z")

    first_version = runs_to_records([first])[0].row_version
    later_version = runs_to_records([later])[0].row_version

    assert later_version == first_version


def test_experiment_traces_are_dropped():
    runs = _trace_runs()
    runs[0]["reference_example_id"] = "ex-1"
    assert runs_to_records(runs) == []


def test_tool_runs_are_tool_calls():
    runs = [
        _run(_ROOT, is_root=True, name="handler", run_type="CHAIN"),
        _run(_CHILD, parent_ids=[_ROOT], name="lookup", run_type="TOOL"),
    ]
    spans = observations_to_span_dicts(runs_to_records(runs), credential=_cred())
    by_name = {s["name"]: s for s in spans}

    assert by_name["lookup"]["span_type"] == "tool_call"
    assert by_name["handler"]["span_type"] == "entry_point"


def test_input_and_output_reach_the_span():
    runs = _trace_runs()
    runs[1] |= {"inputs": {"messages": [{"role": "user", "content": "hi"}]}, "outputs": "hello"}
    records = runs_to_records(runs)
    child = next(r for r in records if r.id == _CHILD)
    assert child.input == {"messages": [{"role": "user", "content": "hi"}]}

    spans = observations_to_span_dicts(records, credential=_cred())
    child_span = next(s for s in spans if s["span_id"] == span_id_for(str(_cred().id), _CHILD))
    assert child_span["attributes"]["overmind.input.data"] == runs[1]["inputs"]
    assert child_span["attributes"]["overmind.output.data"] == "hello"


def test_spans_are_stamped_with_the_langsmith_source():
    records = runs_to_records(_trace_runs())
    spans = observations_to_span_dicts(records, credential=_cred())

    assert {s["attributes"][CONNECTOR_SOURCE_ATTR] for s in spans} == {"langsmith"}
    assert all(s["scope_name"] == "langsmith" for s in spans)
    assert all("langsmith.trace_id" in s["attributes"] for s in spans)


def test_shared_profiler_ranks_langsmith_records_unchanged():
    traces = []
    for i in range(4):
        runs = [
            _run(f"root-{i}", is_root=True, name="handler", run_type="CHAIN"),
            _run(f"capability-{i}", parent_ids=[f"root-{i}"], name="researcher", run_type="CHAIN"),
            _run(f"llm-a-{i}", parent_ids=[f"capability-{i}"], name="chat"),
            _run(f"llm-b-{i}", parent_ids=[f"capability-{i}"], name="chat"),
        ]
        traces.append(runs_to_records(runs))

    shapes = profile_capability_candidates(traces, LANGSMITH)
    names = [s["name"] for s in shapes]

    assert names[0] in {"handler", "researcher"}
    assert names.index("chat") > names.index("researcher")
    assert next(s["parent_name"] for s in shapes if s["name"] == "researcher") == "handler"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
