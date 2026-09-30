import uuid

import pytest
from factories import ingest_spans, make_project

from overbae.models import Capability, IdentityAlias, Span
from overbae.services.capabilities import identity
from overbae.services.connectors.capabilities import resolve_capability as connector_resolve

pytestmark = pytest.mark.django_db


def _capability(project, name, slug=None, **extra) -> Capability:
    return Capability.objects.create(
        project=project, name=name, slug=slug or name.lower().replace(" ", "-"), **extra
    )


def test_save_records_id_name_and_slug_aliases_and_keeps_old_ones_on_rename():
    project = make_project()
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
    project = make_project()
    capability = _capability(project, "Ticket Triage")
    for probe in [str(capability.id), "TICKET TRIAGE", "ticket-triage", "Ticket  Triage"]:
        assert identity.lookup(project.id, probe) == capability, probe
    assert identity.lookup(project.id, "nope") is None
    assert identity.lookup(make_project().id, "ticket-triage") is None


def test_lookup_hides_leftover_unless_asked_and_never_returns_deleted():
    project = make_project()
    old = _capability(project, "Old Triage", status=Capability.Status.LEFTOVER)
    gone = _capability(project, "Gone Triage", status=Capability.Status.DELETED)

    assert identity.lookup(project.id, "Old Triage") is None
    assert identity.lookup(project.id, "Old Triage", include_leftover=True) == old
    assert identity.lookup(project.id, "Gone Triage") is None
    assert identity.lookup(project.id, str(gone.id), include_leftover=True) is None


def _bound(project, attributes, resource=None, *, trace_id=None):
    span_id = uuid.uuid4().hex[:16]
    ingest_spans(
        project,
        [{"name": "chat", "attributes": attributes, "span_id": span_id, "trace_id": trace_id}],
        resource,
    )
    return Span.objects.get(project=project, span_id=span_id).capability


def test_ingest_binds_by_id_only_and_never_mints():
    project = make_project()
    capability = _capability(project, "Ledgerline Invoice Triage")

    assert _bound(project, {"overmind.capability.id": str(capability.id)}) == capability
    # The name is a display label: it never resolves, however exact.
    assert _bound(project, {"overmind.capability.name": "Ledgerline Invoice Triage"}) is None
    assert _bound(project, {"overmind.capability.id": str(uuid.uuid4())}) is None
    assert Capability.objects.filter(project=project).count() == 1


def test_ingest_ignores_the_name_beside_the_id():
    project = make_project()
    pinned = _capability(project, "Pinned")
    other = _capability(project, "Other")

    resource = {"overmind.capability.id": str(pinned.id), "overmind.capability.name": "Other"}
    assert _bound(project, dict(resource), resource) == pinned
    other.refresh_from_db()
    assert other.status == Capability.Status.CURRENT


def test_ingest_renamed_capability_binds_via_resource_id_and_never_via_name():
    project = make_project()
    capability = _capability(project, "Ticket Triage")
    capability.name = "Support Triage"
    capability.save(update_fields=["name"])

    resource = {
        "overmind.capability.id": str(capability.id),
        "overmind.capability.name": "Ticket Triage",
    }
    assert _bound(project, {}, resource) == capability
    name_only = {"overmind.capability.name": "Ticket Triage"}
    assert _bound(project, dict(name_only), name_only) is None


def test_ingest_span_level_id_beats_resource_id():
    project = make_project()
    process_wide = _capability(project, "Process Wide")
    scoped = _capability(project, "Scoped")

    # A multi-capability process: init() pinned one id on the resource, a
    # capability scope stamped a different id on the span.
    resource = {"overmind.capability.id": str(process_wide.id)}
    assert _bound(project, {"overmind.capability.id": str(scoped.id)}, resource) == scoped


def test_ingest_ignores_leftover_and_deleted_rows():
    project = make_project()
    retired = _capability(project, "Retired", status=Capability.Status.LEFTOVER)
    gone = _capability(project, "Gone", status=Capability.Status.DELETED)

    assert _bound(project, {"overmind.capability.id": str(retired.id)}) is None
    assert _bound(project, {"overmind.capability.id": str(gone.id)}) is None
    retired.refresh_from_db()
    assert retired.status == Capability.Status.LEFTOVER


def test_identity_less_spans_join_the_trace_capability_only_when_unambiguous():
    project = make_project()
    alpha = _capability(project, "Alpha")
    beta = _capability(project, "Beta")
    trace_id = uuid.uuid4().hex
    _bound(project, {"overmind.capability.id": str(alpha.id)}, trace_id=trace_id)

    assert _bound(project, {}, trace_id=trace_id) == alpha

    _bound(project, {"overmind.capability.id": str(beta.id)}, trace_id=trace_id)
    assert _bound(project, {}, trace_id=trace_id) is None


def test_resolve_capability_follows_renames():
    from overbae.services.entity_resolution import resolve_capability

    project = make_project()
    capability = _capability(project, "Ticket Triage")
    capability.name = "Support Triage"
    capability.save(update_fields=["name"])
    assert resolve_capability(project, "Ticket Triage") == (capability, None)


def test_connector_mapping_observes_reactivates_and_never_mints_a_peer():
    project = make_project()
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
    project = make_project()
    capability = _capability(project, "Triage")
    capability.slug = "triage-v2"
    capability.save(update_fields=["slug"])
    mapping = {"assignments": {"svc-a": "triage"}, "fallback_capability_id": str(capability.id)}
    assert connector_resolve("svc-a", mapping, project) == capability
    assert connector_resolve(None, mapping, project) == capability
    assert connector_resolve("svc-unknown", mapping, project) == capability
