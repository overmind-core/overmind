"""Per-subtree capability-key assignment for Langfuse observation trees."""

from overbae.services.connectors.capabilities import assign_capability_keys, extract_signal_value
from overbae.services.connectors.langfuse.client import LangFuseObservation


def _obs(**kwargs) -> LangFuseObservation:
    defaults = {
        "id": "x",
        "trace_id": "t1",
        "parent_observation_id": None,
        "type": "SPAN",
        "name": None,
        "start_time": "2026-01-01T00:00:00Z",
        "end_time": None,
    }
    defaults.update(kwargs)
    return LangFuseObservation(**defaults)


def test_per_subtree_nearest_ancestor_capability():
    # supervisor -> tool -> specialist -> generation, both capabilities named boundaries
    tree = [
        _obs(id="sup", type="CAPABILITY", name="supervisor", is_root_observation=True),
        _obs(id="tool", type="TOOL", name="search", parent_observation_id="sup"),
        _obs(id="spec", type="CAPABILITY", name="specialist", parent_observation_id="sup"),
        _obs(id="gen", type="GENERATION", name="llm", parent_observation_id="spec"),
    ]
    keys = assign_capability_keys(
        tree, {"source": "observation_name", "names": ["supervisor", "specialist"]}
    )

    assert keys["sup"] == "supervisor"
    assert keys["tool"] == "supervisor"
    assert keys["spec"] == "specialist"
    assert keys["gen"] == "specialist"


def test_metadata_source_applies_to_whole_trace():
    tree = [
        _obs(id="r", is_root_observation=True, metadata={"capability_name": "billing"}),
        _obs(id="c", parent_observation_id="r"),
    ]
    keys = assign_capability_keys(tree, {"source": "metadata", "key": "capability_name"})
    assert keys == {"r": "billing", "c": "billing"}


def test_extract_tag_prefix():
    tree = [_obs(id="r", is_root_observation=True, tags=["env:prod", "capability:triage"])]
    assert extract_signal_value(tree, "tag", "capability") == "triage"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
