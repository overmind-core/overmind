import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from factories import make_connector, make_project, make_span, make_user, member_client

from overbae.management.commands.seed_demo import SLUG, reset_demo_billing
from overbae.models import BillingTelemetry, Capability, Cell, Dataset
from overbae.services.connectors.capabilities import relabel_connector_capabilities
from overbae.services.connectors.schema import (
    CONNECTOR_CAPABILITY_KEY_ATTR,
    CONNECTOR_CREDENTIAL_ID_ATTR,
)

pytestmark = [pytest.mark.django_db, pytest.mark.covers("lineage")]


@pytest.mark.covers("ledger")
def test_demo_reset_preserves_real_owner_ledger():
    owner = make_user()
    initial = set(BillingTelemetry.objects.values_list("pk", flat=True))
    demo = make_project(slug=SLUG)
    real = make_project()
    keep = [
        BillingTelemetry.objects.create(
            user=owner, project=project, service=service, amount=amount
        ).pk
        for project, service, amount in [
            (real, "evaluation", -2),
            (None, "stripe-topup", 100),
            (None, "free-credits", 10),
        ]
    ]
    BillingTelemetry.objects.create(user=owner, project=demo, service="evaluation", amount=-5)
    BillingTelemetry.objects.create(
        user=owner, service="stripe-topup", amount=50, idempotency_key="topup:cs_live_seed_a1B2c3"
    )
    reset_demo_billing(owner.email)
    assert set(BillingTelemetry.objects.values_list("pk", flat=True)) == set(keep) | initial


@pytest.mark.covers("vendor-connectors")
def test_relabel_clears_removed_assignments_and_handles_missing_agent_keys():
    project = make_project()
    capability = Capability.objects.create(project=project, name="Agent", slug="agent")
    credential = make_connector("langsmith", project=project)
    for attributes in [
        {CONNECTOR_CAPABILITY_KEY_ATTR: "agent"},
        {},
        {CONNECTOR_CAPABILITY_KEY_ATTR: None},
    ]:
        make_span(
            project,
            trace_id="trace",
            capability=capability,
            attributes=attributes,
            resource_attrs={CONNECTOR_CREDENTIAL_ID_ATTR: str(credential.pk)},
        )
    assert relabel_connector_capabilities(credential) == 3
    assert not project.spans.filter(capability__isnull=False).exists()


@pytest.mark.covers("vendor-connectors")
def test_relabel_keeps_name_matched_capabilities_without_a_mapping():
    project = make_project()
    capability = Capability.objects.create(project=project, name="Agent", slug="agent")
    credential = make_connector("langsmith", project=project)
    span = make_span(
        project,
        trace_id="trace",
        name="Agent",
        capability=capability,
        attributes={CONNECTOR_CAPABILITY_KEY_ATTR: "unmapped"},
        resource_attrs={CONNECTOR_CREDENTIAL_ID_ATTR: str(credential.pk)},
    )
    assert relabel_connector_capabilities(credential) == 0
    span.refresh_from_db()
    assert span.capability_id == capability.pk


@pytest.mark.covers("capacity")
def test_capability_list_dataset_queries_stay_bounded_as_the_page_grows():
    project = make_project()
    client = member_client(project)

    def add(index):
        capability = Capability.objects.create(
            project=project, name=f"Agent {index}", slug=f"agent-{index}"
        )
        dataset = Dataset.objects.create(project=project, capability=capability, name="Evaluation")
        Cell.objects.create(
            dataset=dataset,
            position=0,
            title="Ready",
            state="ok",
            rows=7,
            intent_report={"eval": {"has_reference": True}},
        )

    add(0)
    with CaptureQueriesContext(connection) as small:
        response = client.get("/api/capabilities/", {"project": str(project.pk)})
        assert response.status_code == 200, response.data
    for index in range(1, 12):
        add(index)
    with CaptureQueriesContext(connection) as large:
        response = client.get("/api/capabilities/", {"project": str(project.pk)})
        assert response.status_code == 200, response.data
    assert len(large) <= len(small)
    assert len(response.data["results"]) == 12
    assert all(
        row["dataset_size"] == 7 and row["dataset_has_expected_output"]
        for row in response.data["results"]
    )
