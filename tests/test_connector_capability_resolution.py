"""Observation names resolve to Capabilities through recorded names and cards."""

import uuid

import pytest

from overbae.models import Capability, Project
from overbae.services.connectors.capability_resolution import propose_capability_assignments

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


if __name__ == "__main__":
    import pytest as _pytest

    raise SystemExit(_pytest.main([__file__, "-q"]))
