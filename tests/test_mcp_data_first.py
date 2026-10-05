import asyncio
from unittest.mock import patch

import pytest
from test_data_first_workflow import workspace

from overbae.models import APIToken, DataPartitionPlan, Project
from overbae.services.decision_providers import catalog
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)


def test_agent_creates_and_inspects_project_scoped_partition_without_repository():
    project, _, dataset = workspace()
    context = MCPContext(
        user=project.memberships.first().user,
        token=APIToken(scope={"scope": "project", "permission": ["read", "write"]}),
        project=project,
    )
    arguments = {
        "name": "Agent partitions",
        "request_key": "agent-split",
        "source_cell": str(dataset.active_cell.pk),
        "recipe": {"seed": 12, "fractions": {"train": 0.8, "final": 0.2}, "group_by": ["group_id"]},
    }
    with patch("overbae.tasks.data_partitions.build_plan.delay"):
        result = asyncio.run(CATALOG.call("create_data_partition", arguments, context))
    assert not result.isError, result
    plan = DataPartitionPlan.objects.get(project=project)
    result = asyncio.run(
        CATALOG.call("get_job", {"kind": "data_partition", "id": str(plan.pk)}, context)
    )
    assert not result.isError, result
    foreign = Project.objects.create(name="Foreign", slug="foreign-partitions")
    foreign_context = MCPContext(user=context.user, token=context.token, project=foreign)
    denied = asyncio.run(
        CATALOG.call("get_job", {"kind": "data_partition", "id": str(plan.pk)}, foreign_context)
    )
    assert denied.isError


def test_decision_catalog_reports_eligibility_without_claiming_hardware_qualification():
    project, _, _ = workspace()
    context = MCPContext(
        user=project.memberships.first().user,
        token=APIToken(scope={"scope": "project", "permission": ["read"]}),
        project=project,
    )
    result = asyncio.run(CATALOG.call("list_decision_models", {}, context))
    assert not result.isError, result
    options = catalog(project)
    assert any(p["kind"] == "foundation" for p in options)
    assert all(p["qualification"] == "catalog_eligible" for p in options)
