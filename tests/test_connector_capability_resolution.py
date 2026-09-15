"""Observation names resolve to Capabilities through recorded names and cards."""

import uuid

import pytest

from overbae.models import Capability, Project
from overbae.services.connectors.capability_resolution import (
    drop_nested_mapping_names,
    propose_boundary_assignments,
    propose_capability_assignments,
    suggest_parent_boundaries,
    union_custom_mapping,
    unmapped_root_names,
)

pytestmark = pytest.mark.django_db


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _capability(project, name, *, path="", fn="", meta=None) -> Capability:
    return Capability.objects.create(
        project=project,
        name=name,
        slug=name.lower().replace(" ", "-"),
        source_path=path,
        entrypoint_fn=fn,
        improvement_metadata=meta or {},
    )


def test_entrypoint_function_matches_across_separator_style():
    project = _project()
    capability = _capability(project, "Triage", path="app/triage.py", fn="triage_invoices")

    got = propose_capability_assignments(project, ["triage-invoices"])

    assert got["triage-invoices"]["capability_id"] == str(capability.id)
    assert got["triage-invoices"]["method"] == "name"


def test_scanned_modes_and_tools_are_matchable():
    project = _project()
    capability = _capability(
        project,
        "Mailbox",
        meta={"modes": [{"name": "scan-inbox"}], "tool_spec": [{"name": "rank_invoices"}]},
    )

    got = propose_capability_assignments(project, ["scan-inbox", "rank-invoices"])

    assert got["scan-inbox"]["capability_id"] == str(capability.id)
    assert got["rank-invoices"]["capability_id"] == str(capability.id)


def test_unknown_name_yields_no_proposal():
    project = _project()
    _capability(project, "Triage", path="app/triage.py", fn="triage_invoices")

    assert propose_capability_assignments(project, ["something-else"]) == {}


def test_no_capabilities_or_no_keys_is_not_an_error():
    project = _project()
    assert propose_capability_assignments(project, ["anything"]) == {}
    _capability(project, "Triage")
    assert propose_capability_assignments(project, []) == {}


def test_boundary_proposals_ignore_tool_spec():
    project = _project()
    capability = _capability(
        project,
        "Adjudicator",
        fn="adjudicate_claim",
        meta={"tool_spec": [{"name": "get_fx_rate"}]},
    )

    keys = ["adjudicate_claim", "get_fx_rate"]
    assert propose_capability_assignments(project, keys)["get_fx_rate"]["capability_id"] == str(
        capability.id
    )
    assert "get_fx_rate" not in propose_boundary_assignments(project, keys)
    assert propose_boundary_assignments(project, keys)["adjudicate_claim"]["capability_id"] == str(
        capability.id
    )


def test_drop_nested_mapping_names_keeps_the_parent():
    shapes = [
        {"name": "adjudicate_claim", "parent_name": None, "score": 5, "is_root": True},
        {"name": "get_fx_rate", "parent_name": "adjudicate_claim", "score": 0, "is_root": False},
    ]

    mapping, dropped = drop_nested_mapping_names(
        {
            "source": "observation_name",
            "names": ["adjudicate_claim", "get_fx_rate"],
            "assignments": {"adjudicate_claim": "a", "get_fx_rate": "a"},
        },
        shapes,
    )

    assert mapping["names"] == ["adjudicate_claim"]
    assert mapping["assignments"] == {"adjudicate_claim": "a"}
    assert dropped == ["get_fx_rate"]


def test_suggest_parent_boundaries_prefers_entrypoint_over_wrapper():
    project = _project()
    capability = _capability(
        project,
        "Triage Agent",
        fn="analyze_email",
        meta={"modes": [{"name": "triage_invoices"}], "tool_spec": [{"name": "get_fx_rate"}]},
    )
    shapes = [
        {
            "name": "triage_invoices",
            "parent_name": None,
            "score": 8,
            "is_root": True,
            "type": "SPAN",
        },
        {
            "name": "analyze_email",
            "parent_name": "triage_invoices",
            "score": 6,
            "is_root": False,
            "type": "SPAN",
        },
        {
            "name": "get_fx_rate",
            "parent_name": "analyze_email",
            "score": 0,
            "is_root": False,
            "type": "TOOL",
        },
    ]

    suggested = suggest_parent_boundaries(project, shapes)

    assert [item["name"] for item in suggested] == ["analyze_email"]
    assert suggested[0]["capability_id"] == str(capability.id)
    assert suggested[0]["nested_names"] == ["get_fx_rate"]
    assert suggested[0]["alternatives"] == ["triage_invoices"]
    assert unmapped_root_names(shapes, suggested) == ["triage_invoices"]


def test_suggest_parent_boundaries_lists_nested_matches_as_alternatives():
    project = _project()
    capability = _capability(
        project,
        "Invoice triage",
        fn="run_invoice_agent",
        meta={"modes": [{"name": "analyze_email", "entrypoint_fn": "analyze_email"}]},
    )
    shapes = [
        {
            "name": "run_ledgerline",
            "parent_name": None,
            "score": 9,
            "is_root": True,
            "type": "SPAN",
        },
        {
            "name": "run_invoice_agent",
            "parent_name": "run_ledgerline",
            "score": 8,
            "is_root": False,
            "type": "SPAN",
        },
        {
            "name": "analyze_email",
            "parent_name": "run_invoice_agent",
            "score": 6,
            "is_root": False,
            "type": "SPAN",
        },
    ]

    suggested = suggest_parent_boundaries(project, shapes)

    assert [item["name"] for item in suggested] == ["run_invoice_agent"]
    assert suggested[0]["capability_id"] == str(capability.id)
    assert suggested[0]["alternatives"] == ["analyze_email"]
    assert "analyze_email" in suggested[0]["nested_names"]


def test_union_custom_mapping_keeps_caller_names_and_fallback():
    suggested = [
        {
            "name": "adjudicate_claim",
            "capability_id": "cap-a",
            "capability_name": "Adjudicator",
        }
    ]

    filled = union_custom_mapping(
        {
            "assignments": {"scan-inbox": "cap-b", "adjudicate_claim": "cap-a"},
            "fallback_capability_id": "cap-b",
        },
        suggested,
    )

    assert filled["names"] == ["adjudicate_claim", "scan-inbox"]
    assert filled["assignments"]["scan-inbox"] == "cap-b"
    assert filled["fallback_capability_id"] == "cap-b"


def test_drop_nested_keeps_a_nested_name_when_its_ancestor_is_omitted():
    shapes = [
        {"name": "run_invoice_agent", "parent_name": None, "score": 8, "is_root": True},
        {"name": "analyze_email", "parent_name": "run_invoice_agent", "score": 6, "is_root": False},
    ]

    mapping, dropped = drop_nested_mapping_names(
        {
            "source": "observation_name",
            "names": ["analyze_email"],
            "assignments": {"analyze_email": "cap-t"},
        },
        shapes,
    )

    assert mapping["names"] == ["analyze_email"]
    assert dropped == []


if __name__ == "__main__":
    import pytest as _pytest

    raise SystemExit(_pytest.main([__file__, "-q"]))
