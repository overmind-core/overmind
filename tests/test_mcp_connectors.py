from __future__ import annotations

import asyncio
import json
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.models import (
    APIToken,
    Capability,
    ConnectorCredential,
    ConnectorImportPreview,
    ConnectorSyncConfig,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.resources import connector_resource_payload

pytestmark = pytest.mark.django_db(transaction=True)


def _context(*, permission: str | list[str] = "read") -> MCPContext:
    user = User.objects.create_user(
        email=f"mcp-connectors-{uuid.uuid4().hex[:8]}@test.com",
        password="pw",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="Connectors", slug=f"connectors-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    permissions = [permission] if isinstance(permission, str) else permission
    token = APIToken(
        scope={
            "scope": "project",
            "resourceIds": [str(project.id)],
            "permission": permissions,
        }
    )
    return MCPContext(user=user, token=token, project=project)


def _call(name: str, arguments: dict, context: MCPContext):
    return asyncio.run(CATALOG.call(name, arguments, context))


def _connector(
    context: MCPContext, *, configured: bool = True, mapping_confirmed: bool | None = None
) -> ConnectorCredential:
    connector = ConnectorCredential.objects.create(
        project=context.project,
        name=f"Langfuse {uuid.uuid4().hex[:6]}",
        connector_type=ConnectorCredential.ConnectorType.LANGFUSE,
        api_key="provider-key",
        api_secret="provider-secret",
        verified_at=timezone.now(),
    )
    if configured:
        ConnectorSyncConfig.objects.create(
            credential=connector,
            version=1,
            source_project_id="source-project",
            lookback_days=7,
            effective_from=timezone.now(),
        )
    return connector


def test_inspection_is_scoped_and_never_exposes_keys():
    context = _context(permission=["read", "write"])
    connector = _connector(context)
    other = _connector(_context(permission="write"))
    result = _call("inspect_connectors", {"connector": str(connector.id)}, context)
    assert not result.isError, result.structuredContent
    encoded = json.dumps(result.structuredContent)
    assert "provider-key" not in encoded and "provider-secret" not in encoded
    assert result.structuredContent["connector"]["groups"] == []
    result = _call("inspect_connectors", {"connector": str(other.id)}, context)
    assert result.isError


def test_read_permission_cannot_request_an_import_or_review():
    context = _context()
    connector = _connector(context)
    for name, arguments in [
        ("configure_connector", {"connector": str(connector.id)}),
        ("sync_connector", {"connector": str(connector.id)}),
        (
            "review_trace_groups",
            {
                "connector": str(connector.id),
                "assignments": [
                    {"group_id": str(uuid.uuid4()), "capability_id": None, "expected_revision": 1}
                ],
            },
        ),
    ]:
        assert _call(name, arguments, context).isError


def test_preview_is_visible_and_requires_a_ready_receipt(monkeypatch):
    context = _context(permission="write")
    connector = _connector(context, configured=False)
    monkeypatch.setattr(
        "overbae.tasks.connector_review.preview_connector_import.apply_async", lambda **kwargs: None
    )
    monkeypatch.setattr(
        "overbae.tasks.connector_sync.sync_connector_chunk.apply_async", lambda **kwargs: None
    )
    result = _call(
        "configure_connector",
        {"connector": str(connector.id), "source_project_id": "source", "lookback_days": 180},
        context,
    )
    assert not result.isError, result.structuredContent
    preview = ConnectorImportPreview.objects.get(pk=result.structuredContent["preview"]["id"])
    assert preview.window_to - preview.window_from == timedelta(days=180)
    blocked = _call(
        "sync_connector", {"connector": str(connector.id), "preview_id": str(preview.id)}, context
    )
    assert blocked.isError
    preview.status = "ready"
    connector.refresh_from_db()
    preview.credential_updated_at = connector.updated_at
    preview.expires_at = timezone.now() + timedelta(minutes=15)
    preview.trace_count = 42
    preview.save()
    result = _call(
        "sync_connector", {"connector": str(connector.id), "preview_id": str(preview.id)}, context
    )
    assert not result.isError, result.structuredContent
    assert result.structuredContent["queued"]
    connector.refresh_from_db()
    assert connector.backfill_total == 42
    resource = connector_resource_payload(
        context.project, connector, "overmind://connectors/" + str(connector.id)
    )
    assert resource["latest_preview"]["status"] == "imported"


def test_mcp_review_uses_the_same_revision_and_unassigned_contract():
    from overbae.services.connectors.langfuse.mapping import LANGFUSE
    from overbae.services.connectors.mapping import observations_to_span_dicts
    from overbae.services.connectors.records import ObservationRecord
    from overbae.tasks.connector_sync import _upsert_spans

    context = _context(permission="write")
    connector = _connector(context)
    capability = Capability.objects.create(project=context.project, name="Support", slug="support")
    record = ObservationRecord(
        id="root",
        trace_id="trace",
        parent_observation_id=None,
        type="AGENT",
        name="Support",
        start_time="2026-01-02T00:00:00Z",
        end_time="2026-01-02T00:00:01Z",
    )
    _upsert_spans(
        context.project,
        observations_to_span_dicts([record], credential=connector, conventions=LANGFUSE),
        credential=connector,
    )
    group = connector.trace_groups.get()
    result = _call(
        "review_trace_groups",
        {
            "connector": str(connector.id),
            "assignments": [
                {
                    "group_id": str(group.id),
                    "capability_id": str(capability.id),
                    "expected_revision": group.revision,
                }
            ],
        },
        context,
    )
    assert not result.isError, result.structuredContent
    stale = _call(
        "review_trace_groups",
        {
            "connector": str(connector.id),
            "assignments": [
                {
                    "group_id": str(group.id),
                    "capability_id": None,
                    "expected_revision": group.revision,
                }
            ],
        },
        context,
    )
    assert stale.isError
    result = _call(
        "review_trace_groups",
        {
            "connector": str(connector.id),
            "assignments": [
                {
                    "group_id": str(group.id),
                    "capability_id": None,
                    "expected_revision": result.structuredContent["groups"][0]["revision"],
                }
            ],
        },
        context,
    )
    assert not result.isError, result.structuredContent
    assert result.structuredContent["groups"][0]["unreviewed_trace_count"] == 0
    assert result.structuredContent["groups"][0]["capability_id"] is None
