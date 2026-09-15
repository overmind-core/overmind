"""The rebind backfill: spans ingest left unbound bind in place once a scan
or a hand-made capability makes their identity resolvable."""

import uuid

import pytest

from overbae.models import Capability, Project, Span
from overbae.tasks.capability_rebind import rebind_unbound_spans

pytestmark = pytest.mark.django_db


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _span(project, *, name="", attributes=None, capability=None) -> Span:
    return Span.objects.create(
        span_id=uuid.uuid4().hex[:16],
        trace_id=uuid.uuid4().hex,
        project=project,
        capability=capability,
        name=name,
        attributes=attributes or {},
        start_time_ns=1,
        end_time_ns=2,
    )


def test_backlog_binds_by_id_only_and_is_idempotent():
    project = _project()
    capability = Capability.objects.create(project=project, name="Triage", slug="triage")
    by_id = _span(project, attributes={"overmind.capability.id": str(capability.id)})
    by_name = _span(project, attributes={"overmind.capability.name": "Triage"})
    by_span_name = _span(project, name="Triage")

    assert rebind_unbound_spans(project_id=str(project.id)) == {"bound": 1}
    by_id.refresh_from_db()
    assert by_id.capability_id == capability.id
    for span in (by_name, by_span_name):
        span.refresh_from_db()
        assert span.capability_id is None

    assert rebind_unbound_spans(project_id=str(project.id)) == {"bound": 0}


def test_backlog_stamped_with_a_deleted_identity_stays_unbound():
    project = _project()
    gone = Capability.objects.create(project=project, name="Old Triage", slug="old-triage")
    span = _span(project, attributes={"overmind.capability.id": str(gone.id)})
    gone.set_status(Capability.Status.DELETED)

    assert rebind_unbound_spans(project_id=str(project.id)) == {"bound": 0}
    span.refresh_from_db()
    assert span.capability_id is None


def test_leftover_identity_stays_unbound_until_reactivated():
    project = _project()
    capability = Capability.objects.create(
        project=project, name="Judge", slug="judge", status=Capability.Status.LEFTOVER
    )
    span = _span(project, attributes={"overmind.capability.id": str(capability.id)})

    assert rebind_unbound_spans(project_id=str(project.id)) == {"bound": 0}
    capability.set_status(Capability.Status.CURRENT)
    assert rebind_unbound_spans(project_id=str(project.id)) == {"bound": 1}
    span.refresh_from_db()
    assert span.capability_id == capability.id
