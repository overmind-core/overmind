import pytest
from factories import make_connector, make_project, make_span, make_user

from overbae.management.commands.seed_demo import SLUG, reset_demo_billing
from overbae.models import BillingTelemetry, Capability
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
