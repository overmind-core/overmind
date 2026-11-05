import pytest
from factories import make_connector, make_project, make_span

from overbae.models import Capability
from overbae.services.connectors.capabilities import relabel_connector_capabilities
from overbae.services.connectors.schema import (
    CONNECTOR_CAPABILITY_KEY_ATTR,
    CONNECTOR_CREDENTIAL_ID_ATTR,
)

pytestmark = [pytest.mark.django_db, pytest.mark.covers("lineage")]


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
