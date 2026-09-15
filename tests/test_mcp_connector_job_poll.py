from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from django.utils import timezone

from overbae.models import APIToken, ConnectorCredential, Project, ProjectMembership, User
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext

pytestmark = pytest.mark.django_db(transaction=True)


def _context() -> MCPContext:
    user = User.objects.create_user(
        email=f"mcp-connector-job-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(
        name="Connector jobs", slug=f"connector-{uuid.uuid4().hex[:8]}"
    )
    ProjectMembership.objects.create(user=user, project=project)
    token = APIToken(scope={"scope": "project", "permission": ["read"]})
    return MCPContext(user=user, token=token, project=project)


def _call(context: MCPContext, connector_id: str):
    return asyncio.run(
        CATALOG.call(
            "get_job",
            {"kind": "connector_sync", "id": connector_id},
            context,
        )
    )


def test_connector_sync_get_job_uses_connector_receipt_id_and_resource():
    context = _context()
    synced_at = timezone.now()
    connector = ConnectorCredential.objects.create(
        project=context.project,
        name="Production traces",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        sync_status=ConnectorCredential.SyncStatus.BACKFILLING,
        last_synced_at=synced_at,
        backfill_imported=7,
        backfill_total=12,
        total_spans_imported=20,
        total_traces_imported=8,
    )

    result = _call(context, str(connector.id))

    assert result.isError is False, result.structuredContent
    output = result.structuredContent
    assert output["kind"] == "connector_sync"
    assert output["id"] == str(connector.id)
    assert output["status"] == ConnectorCredential.SyncStatus.BACKFILLING
    assert output["label"] == connector.name
    assert output["resource"]["uri"] == f"overmind://connectors/{connector.id}"
    assert output["resource_links"] == [output["resource"]]
    assert output["details"]["sync"] == {
        "status": ConnectorCredential.SyncStatus.BACKFILLING,
        "auto_sync_enabled": False,
        "poll_interval_seconds": 300,
        "last_synced_at": synced_at.isoformat().replace("+00:00", "Z"),
        "next_poll_at": None,
        "backfill_imported": 7,
        "backfill_total": 12,
        "total_spans_imported": 20,
        "total_traces_imported": 8,
        "has_error": False,
    }


def test_connector_sync_get_job_redacts_aggregate_sync_error():
    context = _context()
    connector = ConnectorCredential.objects.create(
        project=context.project,
        name="Broken traces",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        sync_status=ConnectorCredential.SyncStatus.ERROR,
        sync_error="provider-secret-value",
    )

    result = _call(context, str(connector.id))

    assert result.isError is False, result.structuredContent
    assert result.structuredContent["job_error"] == "Connector sync failed."
    assert result.structuredContent["details"]["sync"]["has_error"] is True
    assert "provider-secret-value" not in json.dumps(result.structuredContent)


def test_connector_sync_get_job_is_project_scoped_and_requires_uuid():
    context = _context()
    other = Project.objects.create(name="Other", slug=f"other-{uuid.uuid4().hex[:8]}")
    connector = ConnectorCredential.objects.create(
        project=other,
        name="Other traces",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
    )

    foreign = _call(context, str(connector.id))
    malformed = _call(context, "not-a-uuid")

    assert foreign.isError is True
    assert foreign.structuredContent["error"]["code"] == "resource_not_found"
    assert malformed.isError is True
    assert malformed.structuredContent["error"]["code"] == "resource_not_found"
