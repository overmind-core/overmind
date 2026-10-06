"""API tests for POST|GET /api/v1/sync — AgentManifest from decorator scan."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from overbae.models import (
    APIToken,
    Behaviour,
    Capability,
    Project,
    ProjectMembership,
    User,
)

PROJECT_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture
def project(db):
    return Project.objects.create(pk=PROJECT_ID, name="demo", slug="demo")


@pytest.fixture
def client(project):
    user = User.objects.create_user(
        email=f"{uuid.uuid4().hex}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    ProjectMembership.objects.create(user=user, project=project)
    raw_key, _token = APIToken.create_for_user(user, project=project)
    c = APIClient()
    c.credentials(HTTP_X_API_KEY=raw_key)
    return c


def _sym(**overrides):
    body = {
        "qualname": "agent.handle",
        "file": "agent.py",
        "line_start": 1,
        "line_end": 10,
        "role": "capability",
        "slug": "support-agent",
        "name": "Support Agent",
        "description": "Handle support tickets",
        "signature": {
            "params": [{"name": "ticket", "type": "str", "required": True}],
            "returns": "str",
        },
        "calls": ["agent.lookup"],
        "expectations": [],
        "unresolved": [],
    }
    body.update(overrides)
    return body


def _tool(**overrides):
    body = {
        "qualname": "agent.lookup",
        "file": "agent.py",
        "line_start": 20,
        "line_end": 30,
        "role": "tool",
        "capability": "support-agent",
        "name": "lookup",
        "description": "Look up an order",
        "signature": {
            "params": [{"name": "order_id", "type": "str", "required": True}],
            "returns": "dict",
        },
        "calls": [],
        "expectations": [],
        "unresolved": [],
    }
    body.update(overrides)
    return body


def _task(**overrides):
    body = {
        "qualname": "agent.handle",
        "file": "agent.py",
        "line_start": 5,
        "line_end": 8,
        "role": "task",
        "capability": "support-agent",
        "task_key": "refund-flow",
        "unit": "turn",
        "name": "refund-flow",
        "signature": {"params": [], "returns": ""},
        "calls": ["agent.lookup"],
        "expectations": [],
        "unresolved": [],
    }
    body.update(overrides)
    return body


def _manifest(symbols):
    return {
        "project_id": str(PROJECT_ID),
        "repo_summary": "invoice bot",
        "trace_provider": "overmind",
        "version": "0.3.0",
        "symbols": symbols,
    }


@pytest.fixture(autouse=True)
def _quiet_side_effects():
    with (
        patch("overbae.services.agent_manifest.identity.enqueue_rebind"),
        patch(
            "overbae.services.agent_manifest.author_card",
            side_effect=lambda derived, evidence=None: {
                **derived,
                "task": derived.get("task") or "derived task",
                "domain": "support",
                "modality": "text",
                "expected_output": "a reply",
                "success_criteria": ["resolves the ticket"],
                "failure_modes": ["wrong team"],
                "constraints": [],
                "generator": "derive_card@v1",
            },
        ),
        patch("overbae.tasks.eval.preload_capability_eval_set.delay"),
    ):
        yield


@pytest.mark.django_db
def test_scan_provenance_roundtrips_and_sync_time_is_server_owned(client, project):
    source = {
        "repository": "acme/agent",
        "directory": ".",
        "branch": "main",
        "commit": "a" * 40,
        "dirty": True,
        "fingerprint": "b" * 64,
        "scanned_at": "2026-09-20T12:00:00+00:00",
    }
    body = {
        **_manifest([_sym(), _tool()]),
        "repository_snapshot": source,
        "last_synced_at": "2000-01-01T00:00:00Z",
    }
    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    with patch("overbae.services.agent_manifest.timezone.now", return_value=now):
        response = client.post("/api/v1/sync", body, format="json")
    assert response.status_code == 200, response.data
    assert response.data["repository_snapshot"] == source
    assert response.data["last_synced_at"] == "2026-09-27T12:00:00+00:00"
    pulled = client.get("/api/v1/sync", {"project_id": str(project.id)}).data
    assert pulled["repository_snapshot"] == source
    graph = client.get("/api/agent/", {"project": str(project.id)}).data
    assert graph["repository_snapshot"] == source
    assert graph["last_synced_at"] == response.data["last_synced_at"]
    response = client.post("/api/v1/sync", _manifest([_sym(), _tool()]), format="json")
    assert response.data["repository_snapshot"] is None


@pytest.mark.django_db
def test_invalid_provenance_does_not_replace_project_snapshot(client, project):
    response = client.post(
        "/api/v1/sync",
        {**_manifest([]), "repository_snapshot": {"commit": "not-a-sha"}},
        format="json",
    )
    assert response.status_code == 400
    project.refresh_from_db()
    assert not project.settings.get("last_synced_at")


@pytest.mark.django_db
def test_missing_api_key_is_unauthorized():
    resp = APIClient().post("/api/v1/sync", _manifest([_sym()]), format="json")
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_post_assigns_ids_and_get_roundtrips(client):
    posted = client.post("/api/v1/sync", _manifest([_sym(), _tool(), _task()]), format="json")
    assert posted.status_code == 200, posted.data
    caps = posted.data["capabilities"]
    assert len(caps) == 1
    cap_id = caps[0]["id"]
    assert cap_id
    assert caps[0]["slug"] == "support-agent"
    assert caps[0]["status"] == "active"
    assert caps[0]["capability_card"].get("generator") == "derive_card@v1"
    assert Behaviour.objects.filter(capability_id=cap_id, key="refund-flow").exists()

    pulled = client.get("/api/v1/sync", {"project_id": str(PROJECT_ID)})
    assert pulled.status_code == 200
    assert pulled.data["capabilities"][0]["id"] == cap_id
    assert pulled.data["repo_summary"] == "invoice bot"
    assert pulled.data["version"] == "0.3.0"


@pytest.mark.django_db
def test_post_same_slug_keeps_id(client):
    first = client.post("/api/v1/sync", _manifest([_sym()]), format="json")
    cap_id = first.data["capabilities"][0]["id"]
    second = client.post(
        "/api/v1/sync",
        _manifest([_sym(name="Support Agent 2")]),
        format="json",
    )
    assert second.data["capabilities"][0]["id"] == cap_id
    assert second.data["capabilities"][0]["name"] == "Support Agent 2"
    assert Capability.objects.filter(project_id=PROJECT_ID).count() == 1


@pytest.mark.django_db
def test_absent_slug_becomes_leftover_and_is_not_in_post_response(client):
    client.post(
        "/api/v1/sync",
        _manifest([_sym(), _sym(slug="other", name="Other", qualname="agent.other")]),
        format="json",
    )
    posted = client.post("/api/v1/sync", _manifest([_sym()]), format="json")
    slugs = {c["slug"] for c in posted.data["capabilities"]}
    assert slugs == {"support-agent"}
    leftover = Capability.objects.get(project_id=PROJECT_ID, slug="other")
    assert leftover.status == Capability.Status.LEFTOVER

    pulled = client.get("/api/v1/sync", {"project_id": str(PROJECT_ID)})
    by_slug = {c["slug"]: c for c in pulled.data["capabilities"]}
    assert by_slug["other"]["status"] == "leftover"
    assert by_slug["other"]["archived"] is True
    assert by_slug["support-agent"]["status"] == "active"
    assert by_slug["support-agent"]["archived"] is False


@pytest.mark.django_db
def test_observed_capability_is_not_marked_leftover(client):
    client.post("/api/v1/sync", _manifest([_sym()]), format="json")
    observed = Capability.objects.create(
        project_id=PROJECT_ID,
        slug="runtime-only",
        name="Runtime Only",
        observed=True,
        status=Capability.Status.CURRENT,
    )
    client.post("/api/v1/sync", _manifest([_sym()]), format="json")
    observed.refresh_from_db()
    assert observed.status == Capability.Status.CURRENT


@pytest.mark.django_db
def test_leftover_slug_is_remounted(client):
    client.post("/api/v1/sync", _manifest([_sym()]), format="json")
    client.post("/api/v1/sync", _manifest([]), format="json")
    leftover = Capability.objects.get(slug="support-agent")
    assert leftover.status == Capability.Status.LEFTOVER
    cap_id = str(leftover.id)

    remounted = client.post("/api/v1/sync", _manifest([_sym()]), format="json")
    assert remounted.data["capabilities"][0]["id"] == cap_id
    leftover.refresh_from_db()
    assert leftover.status == Capability.Status.CURRENT


@pytest.mark.django_db
def test_wrong_project_id_is_403(client):
    other = str(uuid.uuid4())
    posted = client.post(
        "/api/v1/sync",
        {**_manifest([_sym()]), "project_id": other},
        format="json",
    )
    assert posted.status_code == 403
    pulled = client.get("/api/v1/sync", {"project_id": other})
    assert pulled.status_code == 403


@pytest.mark.django_db
def test_account_key_can_sync_a_membership_project(project):
    user = User.objects.create_user(
        email=f"{uuid.uuid4().hex}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    ProjectMembership.objects.create(user=user, project=project)
    raw_key, _token = APIToken.create_for_user(user)
    c = APIClient()
    c.credentials(HTTP_X_API_KEY=raw_key)
    posted = c.post("/api/v1/sync", _manifest([_sym()]), format="json")
    assert posted.status_code == 200, posted.data


@pytest.mark.django_db
def test_derived_card_and_behaviours_from_task(client, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        posted = client.post(
            "/api/v1/sync",
            _manifest([_sym(), _tool(), _task()]),
            format="json",
        )
    assert posted.status_code == 200, posted.data
    cap = Capability.objects.get(slug="support-agent")
    card = cap.improvement_metadata["capability_card"]
    assert card["generator"] == "derive_card@v1"
    assert "ticket" in (card.get("input_schema") or {})
    assert Behaviour.objects.filter(capability=cap, key="refund-flow").exists()
    graph = client.get("/api/agent/", {"project": str(PROJECT_ID)}).data
    assert graph["capabilities"]
    assert isinstance(graph.get("edges"), list)


@pytest.mark.django_db
def test_sync_enqueues_preload_on_commit(client, django_capture_on_commit_callbacks):
    with (
        patch("overbae.tasks.eval.preload_capability_eval_set.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        posted = client.post("/api/v1/sync", _manifest([_sym()]), format="json")
    assert posted.status_code == 200, posted.data
    cap = Capability.objects.get(slug="support-agent")
    delay.assert_called_once_with(capability_id=str(cap.id))
