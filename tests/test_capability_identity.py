import uuid

import pytest

from overbae.api.otlp import _resolve_capability
from overbae.models import Capability, IdentityAlias, Project
from overbae.services.capabilities import identity
from overbae.services.connectors.capabilities import resolve_capability as connector_resolve

pytestmark = pytest.mark.django_db


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _capability(project, name, slug=None, **extra) -> Capability:
    return Capability.objects.create(
        project=project, name=name, slug=slug or name.lower().replace(" ", "-"), **extra
    )


def test_save_records_id_name_and_slug_aliases_and_keeps_old_ones_on_rename():
    project = _project()
    capability = _capability(project, "Ticket Triage")
    assert set(
        IdentityAlias.objects.filter(capability=capability).values_list("value", flat=True)
    ) == {
        str(capability.id),
        "ticket triage",
        "ticket-triage",
    }
    capability.name = "Support Triage"
    capability.save(update_fields=["name"])
    values = set(
        IdentityAlias.objects.filter(capability=capability).values_list("value", flat=True)
    )
    assert {"ticket triage", "support triage"} <= values
    # The slug is untouched by a rename and keeps resolving.
    assert "ticket-triage" in values
    assert identity.lookup(project.id, "ticket-triage") == capability


def test_lookup_resolves_id_name_slug_and_slugified_name_case_insensitively():
    project = _project()
    capability = _capability(project, "Ticket Triage")
    for probe in [str(capability.id), "TICKET TRIAGE", "ticket-triage", "Ticket  Triage"]:
        assert identity.lookup(project.id, probe) == capability, probe
    assert identity.lookup(project.id, "nope") is None
    assert identity.lookup(_project().id, "ticket-triage") is None


def test_lookup_hides_leftover_unless_asked_and_never_returns_deleted():
    project = _project()
    old = _capability(project, "Old Triage", status=Capability.Status.LEFTOVER)
    gone = _capability(project, "Gone Triage", status=Capability.Status.DELETED)

    assert identity.lookup(project.id, "Old Triage") is None
    assert identity.lookup(project.id, "Old Triage", include_leftover=True) == old
    assert identity.lookup(project.id, "Gone Triage") is None
    assert identity.lookup(project.id, str(gone.id), include_leftover=True) is None


def test_ingest_binds_by_id_only_and_never_mints():
    project = _project()
    capability = _capability(project, "Ledgerline Invoice Triage")

    assert (
        _resolve_capability(project, {}, {"overmind.capability.id": str(capability.id)})
        == capability
    )
    # The name is a display label: it never resolves, however exact.
    name_only = {"overmind.capability.name": "Ledgerline Invoice Triage"}
    assert _resolve_capability(project, {}, name_only) is None
    assert _resolve_capability(project, {}, {"overmind.capability.id": str(uuid.uuid4())}) is None
    assert Capability.objects.filter(project=project).count() == 1


def test_ingest_ignores_the_name_beside_the_id():
    project = _project()
    pinned = _capability(project, "Pinned")
    other = _capability(project, "Other")

    resource = {"overmind.capability.id": str(pinned.id), "overmind.capability.name": "Other"}
    assert _resolve_capability(project, resource, dict(resource)) == pinned
    assert other.status == Capability.Status.CURRENT


def test_ingest_renamed_capability_binds_via_resource_id_and_never_via_name():
    project = _project()
    capability = _capability(project, "Ticket Triage")
    capability.name = "Support Triage"
    capability.save(update_fields=["name"])

    resource = {
        "overmind.capability.id": str(capability.id),
        "overmind.capability.name": "Ticket Triage",
    }
    assert _resolve_capability(project, resource, dict(resource)) == capability
    name_only = {"overmind.capability.name": "Ticket Triage"}
    assert _resolve_capability(project, name_only, dict(name_only)) is None


def test_ingest_span_level_id_beats_resource_id():
    project = _project()
    process_wide = _capability(project, "Process Wide")
    scoped = _capability(project, "Scoped")

    # A multi-capability process: init() pinned one id on the resource, a
    # capability scope stamped a different id on the span.
    resource = {"overmind.capability.id": str(process_wide.id)}
    tags = dict(resource) | {"overmind.capability.id": str(scoped.id)}
    assert _resolve_capability(project, resource, tags) == scoped


def test_ingest_ignores_leftover_and_deleted_rows():
    project = _project()
    retired = _capability(project, "Retired", status=Capability.Status.LEFTOVER)
    gone = _capability(project, "Gone", status=Capability.Status.DELETED)

    assert _resolve_capability(project, {}, {"overmind.capability.id": str(retired.id)}) is None
    assert _resolve_capability(project, {}, {"overmind.capability.id": str(gone.id)}) is None
    assert retired.status == Capability.Status.LEFTOVER


def test_resolve_capability_follows_renames():
    from overbae.services.entity_resolution import resolve_capability

    project = _project()
    capability = _capability(project, "Ticket Triage")
    capability.name = "Support Triage"
    capability.save(update_fields=["name"])
    assert resolve_capability(project, "Ticket Triage") == (capability, None)


def test_connector_mapping_observes_reactivates_and_never_mints_a_peer():
    project = _project()
    mapping = {"auto_create": True, "assignments": {}}

    fresh = connector_resolve("Checkout Bot", mapping, project)
    assert fresh.observed is True and fresh.status == Capability.Status.CURRENT
    assert connector_resolve("checkout bot", mapping, project) == fresh

    fresh.status = Capability.Status.LEFTOVER
    fresh.save(update_fields=["status"])
    assert connector_resolve("Checkout Bot", mapping, project) == fresh
    fresh.refresh_from_db()
    assert fresh.status == Capability.Status.CURRENT

    assert connector_resolve("Checkout Bot", {"auto_create": False}, project) is None
    assert Capability.objects.filter(project=project).count() == 1


def test_connector_assignment_and_fallback_resolve_through_aliases():
    project = _project()
    capability = _capability(project, "Triage")
    capability.slug = "triage-v2"
    capability.save(update_fields=["slug"])
    mapping = {"assignments": {"svc-a": "triage"}, "fallback_capability_id": str(capability.id)}
    assert connector_resolve("svc-a", mapping, project) == capability
    assert connector_resolve(None, mapping, project) == capability
    assert connector_resolve("svc-unknown", mapping, project) == capability
