"""Galileo trace tree -> record flattening, and reuse of the shared span mapping."""

from types import SimpleNamespace

import pytest

from overbae.services.connectors.galileo.mapping import GALILEO, tree_to_records
from overbae.services.connectors.mapping import observations_to_span_dicts as _to_span_dicts
from overbae.services.connectors.profiling import profile_capability_candidates
from overbae.services.connectors.schema import CONNECTOR_SOURCE_ATTR, CONNECTOR_VERSION_ATTR
from overbae.services.connectors.spans import span_id_for


def observations_to_span_dicts(observations, **kwargs):
    return _to_span_dicts(observations, conventions=GALILEO, **kwargs)


def _cred():
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        name="demo",
        project=SimpleNamespace(id="p", slug="p"),
        capability_mapping={},
    )


def _leaf(**overrides):
    node = {
        "id": "llm-1",
        "type": "llm",
        "name": "chat",
        "model": "gpt-4o",
        "created_at": "2026-01-02T00:00:01Z",
        "updated_at": "2026-01-02T00:00:02Z",
        "metrics": {
            "duration_ns": 1_000_000_000,
            "num_input_tokens": 10,
            "num_output_tokens": 5,
            "num_total_tokens": 15,
        },
        "input": "hi",
        "output": "hello",
    }
    node.update(overrides)
    return node


def _trace(spans=None, **overrides):
    tree = {
        "id": "trace-1",
        "type": "trace",
        "name": "handler",
        "created_at": "2026-01-02T00:00:00Z",
        "updated_at": "2026-01-02T00:00:03Z",
        "session_id": "trace-1",
        "metrics": {"duration_ns": 3_000_000_000},
        "spans": spans if spans is not None else [_leaf()],
    }
    tree.update(overrides)
    return tree


def test_the_root_is_a_trace_type_observation_rooting_the_others():
    records = tree_to_records(_trace())
    root = next(r for r in records if r.is_root_observation)

    assert root.id == "trace-1"
    assert root.type == "TRACE"
    assert root.parent_observation_id is None
    child = next(r for r in records if r.id == "llm-1")
    assert child.parent_observation_id == "trace-1"
    assert child.type == "LLM"


def test_nested_spans_wire_their_parent_from_the_enclosing_node():
    tree = _trace(
        spans=[
            {
                "id": "agent-1",
                "type": "agent",
                "name": "planner",
                "created_at": "2026-01-02T00:00:01Z",
                "updated_at": "2026-01-02T00:00:02Z",
                "metrics": {"duration_ns": 2_000_000_000},
                "spans": [_leaf()],
            }
        ]
    )
    records = tree_to_records(tree)
    by_id = {r.id: r for r in records}

    assert by_id["agent-1"].parent_observation_id == "trace-1"
    assert by_id["llm-1"].parent_observation_id == "agent-1"


def test_end_time_is_created_at_plus_duration_ns():
    records = tree_to_records(_trace())
    leaf = next(r for r in records if r.id == "llm-1")

    assert leaf.start_time == "2026-01-02T00:00:01+00:00"
    assert leaf.end_time == "2026-01-02T00:00:02+00:00"


def test_missing_duration_falls_back_to_a_zero_length_span():
    tree = _trace(spans=[_leaf(metrics={})])
    leaf = next(r for r in tree_to_records(tree) if r.id == "llm-1")

    assert leaf.end_time == leaf.start_time


def test_llm_tokens_map_onto_usage_details():
    leaf = next(r for r in tree_to_records(_trace()) if r.id == "llm-1")
    assert leaf.usage_details == {"input": 10, "output": 5, "total": 15}


def test_row_version_comes_from_updated_at_and_a_missing_one_is_zero():
    with_update = tree_to_records(_trace())[0]
    without_update = tree_to_records(_trace(updated_at=None))[0]

    assert int(with_update.row_version) > 0
    assert without_update.row_version == "0"


def test_a_session_id_equal_to_the_trace_id_is_not_a_session():
    records = tree_to_records(_trace())
    assert {r.session_id for r in records} == {None}


def test_a_distinct_session_id_reaches_every_record():
    records = tree_to_records(_trace(session_id="session-42"))
    assert {r.session_id for r in records} == {"session-42"}

    spans = observations_to_span_dicts(records, credential=_cred())
    assert all(s["attributes"]["conversation.id"] == "session-42" for s in spans)


def test_dataset_fields_do_not_exclude_log_stream_traces():
    assert tree_to_records(_trace(dataset_input="q"))
    assert tree_to_records(_trace(dataset_output="a"))


def test_a_tree_without_an_id_yields_nothing():
    assert tree_to_records({}) == []


def test_error_message_becomes_an_error_level_and_otel_status():
    tree = _trace(spans=[_leaf(error_message="boom")])
    records = tree_to_records(tree)
    leaf = next(r for r in records if r.id == "llm-1")

    assert leaf.level == "ERROR"
    assert "boom" in leaf.status_message

    spans = observations_to_span_dicts(records, credential=_cred())
    leaf_span = next(s for s in spans if s["span_id"] == span_id_for(str(_cred().id), "llm-1"))
    assert leaf_span["status_code"] == 2


def test_error_status_code_becomes_an_error_without_a_message():
    tree = _trace(spans=[_leaf(status_code=500)])
    records = tree_to_records(tree)
    leaf = next(r for r in records if r.id == "llm-1")

    assert leaf.level == "ERROR"
    assert leaf.status_message == "Galileo status code 500"
    spans = observations_to_span_dicts(records, credential=_cred())
    leaf_span = next(s for s in spans if s["span_id"] == span_id_for(str(_cred().id), "llm-1"))
    assert leaf_span["status_code"] == 2


def test_tool_spans_are_tool_calls_and_the_root_is_the_entry_point():
    tree = _trace(
        spans=[
            {
                "id": "tool-1",
                "type": "tool",
                "name": "lookup",
                "created_at": "2026-01-02T00:00:01Z",
                "updated_at": "2026-01-02T00:00:02Z",
                "metrics": {"duration_ns": 500_000_000},
            }
        ]
    )
    spans = observations_to_span_dicts(tree_to_records(tree), credential=_cred())
    by_name = {s["name"]: s for s in spans}

    assert by_name["lookup"]["span_type"] == "tool_call"
    assert by_name["handler"]["span_type"] == "entry_point"


def test_input_and_output_reach_the_span():
    records = tree_to_records(_trace())
    spans = observations_to_span_dicts(records, credential=_cred())
    leaf_span = next(s for s in spans if s["span_id"] == span_id_for(str(_cred().id), "llm-1"))

    assert leaf_span["attributes"]["overmind.input.data"] == "hi"
    assert leaf_span["attributes"]["overmind.output.data"] == "hello"


def test_spans_are_stamped_with_the_galileo_source():
    spans = observations_to_span_dicts(tree_to_records(_trace()), credential=_cred())

    assert {s["attributes"][CONNECTOR_SOURCE_ATTR] for s in spans} == {"galileo"}
    assert all(s["scope_name"] == "galileo" for s in spans)


def test_row_version_reaches_the_span_attribute():
    records = tree_to_records(_trace())
    spans = observations_to_span_dicts(records, credential=_cred())
    root_version = next(r for r in records if r.is_root_observation).row_version

    root_span = next(s for s in spans if s["parent_span_id"] is None)
    assert root_span["attributes"][CONNECTOR_VERSION_ATTR] == root_version


def test_agent_spans_are_ranked_as_the_capability_boundary():
    traces = []
    for i in range(4):
        tree = _trace(
            id=f"trace-{i}",
            spans=[
                {
                    "id": f"agent-{i}",
                    "type": "agent",
                    "name": "researcher",
                    "created_at": "2026-01-02T00:00:01Z",
                    "updated_at": "2026-01-02T00:00:02Z",
                    "metrics": {"duration_ns": 2_000_000_000},
                    "spans": [_leaf(id=f"llm-{i}")],
                }
            ],
        )
        traces.append(tree_to_records(tree))

    shapes = profile_capability_candidates(traces, GALILEO)
    researcher = next(s for s in shapes if s["name"] == "researcher")
    assert researcher["type"] == "AGENT"
    assert shapes[0]["name"] in {"handler", "researcher"}


def test_same_name_trace_wrapper_is_not_a_boundary_when_an_agent_exists():
    tree = _trace(
        name="adjudicate_claim",
        spans=[
            {
                "id": "agent-1",
                "type": "agent",
                "name": "adjudicate_claim",
                "created_at": "2026-01-02T00:00:01Z",
                "updated_at": "2026-01-02T00:00:02Z",
                "metrics": {"duration_ns": 2_000_000_000},
                "spans": [_leaf()],
            }
        ],
    )
    cred = _cred()
    spans = observations_to_span_dicts(
        tree_to_records(tree),
        credential=cred,
        mapping={"source": "observation_name", "names": ["adjudicate_claim"]},
    )
    mapped_roots = [
        span
        for span in spans
        if span["parent_span_id"] is None
        and span["attributes"].get("connector.agent_key") == "adjudicate_claim"
    ]

    assert len(mapped_roots) == 1
    assert mapped_roots[0]["span_id"] == span_id_for(str(cred.id), "agent-1")


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
