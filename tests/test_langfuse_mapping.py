"""Checks for credential-scoped Langfuse observation → span mapping."""

from types import SimpleNamespace

from overbae.services.connectors.langfuse.client import LangFuseObservation
from overbae.services.connectors.langfuse.mapping import LANGFUSE
from overbae.services.connectors.mapping import observations_to_span_dicts
from overbae.services.connectors.schema import (
    CONNECTOR_CAPABILITY_KEY_ATTR,
    CONNECTOR_CREDENTIAL_ID_ATTR,
)
from overbae.services.connectors.spans import span_id_for


def _cred():
    return SimpleNamespace(
        id="11111111-1111-1111-1111-111111111111",
        name="demo",
        project=SimpleNamespace(id="p", slug="p"),
        capability_mapping={},
    )


def test_same_observation_same_span_id():
    a = span_id_for("cred-a", "obs-1")
    b = span_id_for("cred-a", "obs-1")
    c = span_id_for("cred-b", "obs-1")
    assert a == b
    assert a != c


def test_tree_maps_parent_and_entry_point(monkeypatch):
    monkeypatch.setattr(
        "overbae.services.connectors.mapping.resolve_capability",
        lambda *a, **k: None,
    )
    tree = [
        LangFuseObservation(
            id="root",
            trace_id="t1",
            parent_observation_id=None,
            type="CAPABILITY",
            name="supervisor",
            start_time="2026-01-02T00:00:00Z",
            end_time="2026-01-02T00:00:05Z",
            latency=5.0,
            is_root_observation=True,
        ),
        LangFuseObservation(
            id="child",
            trace_id="t1",
            parent_observation_id="root",
            type="GENERATION",
            name="llm",
            start_time="2026-01-02T00:00:01Z",
            end_time="2026-01-02T00:00:02Z",
            latency=1.0,
            model="gpt-4o",
            usage_details={"input": 10, "output": 20, "total": 30},
        ),
    ]
    spans = observations_to_span_dicts(tree, credential=_cred(), conventions=LANGFUSE)
    by_name = {s["name"]: s for s in spans}

    assert by_name["supervisor"]["span_type"] == "entry_point"
    assert by_name["llm"]["span_type"] == "llm_call"
    assert by_name["llm"]["parent_span_id"] == by_name["supervisor"]["span_id"]
    assert by_name["supervisor"]["resource_attrs"][CONNECTOR_CREDENTIAL_ID_ATTR]
    # latency in seconds → duration_ns
    assert by_name["supervisor"]["duration_ns"] == 5_000_000_000


def test_capability_key_stamped_when_mapping_set(monkeypatch):
    monkeypatch.setattr(
        "overbae.services.connectors.mapping.resolve_capability",
        lambda *a, **k: None,
    )
    cred = _cred()
    cred.capability_mapping = {
        "source": "observation_name",
        "names": ["billing"],
        "assignments": {},
    }
    tree = [
        LangFuseObservation(
            id="root",
            trace_id="t1",
            parent_observation_id=None,
            type="CAPABILITY",
            name="billing",
            start_time="2026-01-01T00:00:00Z",
            end_time=None,
            is_root_observation=True,
        )
    ]
    spans = observations_to_span_dicts(tree, credential=cred, conventions=LANGFUSE)
    assert spans[0]["attributes"][CONNECTOR_CAPABILITY_KEY_ATTR] == "billing"


if __name__ == "__main__":
    import pytest

    raise SystemExit(pytest.main([__file__, "-q"]))
