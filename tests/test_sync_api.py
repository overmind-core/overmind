"""API tests for POST|GET /api/v1/sync."""

from __future__ import annotations

import uuid
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


def _cap(**overrides):
    body = {
        "slug": "support-agent",
        "name": "Support Agent",
        "model": "gpt-5-mini",
        "eval_metrics": [{"name": "Task Completion", "type": "managed"}],
    }
    body.update(overrides)
    return body


def _snapshot(capabilities):
    return {
        "project_id": str(PROJECT_ID),
        "repo_summary": "invoice bot",
        "trace_provider": "overmind",
        "version": "0.2.1",
        "capabilities": capabilities,
    }


@pytest.fixture(autouse=True)
def _quiet_side_effects():
    with (
        patch("overbae.services.sync.identity.enqueue_rebind"),
    ):
        yield


@pytest.mark.django_db
def test_missing_api_key_is_unauthorized():
    resp = APIClient().post("/api/v1/sync", _snapshot([_cap()]), format="json")
    assert resp.status_code in (401, 403)


@pytest.mark.django_db
def test_post_assigns_ids_and_get_roundtrips(client):
    posted = client.post("/api/v1/sync", _snapshot([_cap()]), format="json")
    assert posted.status_code == 200, posted.data
    caps = posted.data["capabilities"]
    assert len(caps) == 1
    cap_id = caps[0]["id"]
    assert cap_id
    assert caps[0]["slug"] == "support-agent"
    assert caps[0]["eval_metrics"][0]["name"] == "Task Completion"
    assert caps[0]["status"] == "active"

    pulled = client.get("/api/v1/sync", {"project_id": str(PROJECT_ID)})
    assert pulled.status_code == 200
    assert pulled.data["capabilities"][0]["id"] == cap_id
    assert pulled.data["repo_summary"] == "invoice bot"
    assert pulled.data["version"] == "0.2.1"


@pytest.mark.django_db
def test_post_same_slug_keeps_id(client):
    first = client.post("/api/v1/sync", _snapshot([_cap()]), format="json")
    cap_id = first.data["capabilities"][0]["id"]
    second = client.post("/api/v1/sync", _snapshot([_cap(name="Support Agent 2")]), format="json")
    assert second.data["capabilities"][0]["id"] == cap_id
    assert second.data["capabilities"][0]["name"] == "Support Agent 2"
    assert Capability.objects.filter(project_id=PROJECT_ID).count() == 1


@pytest.mark.django_db
def test_absent_slug_becomes_leftover_and_is_not_in_post_response(client):
    client.post(
        "/api/v1/sync", _snapshot([_cap(), _cap(slug="other", name="Other")]), format="json"
    )
    posted = client.post("/api/v1/sync", _snapshot([_cap()]), format="json")
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
    client.post("/api/v1/sync", _snapshot([_cap()]), format="json")
    observed = Capability.objects.create(
        project_id=PROJECT_ID,
        slug="runtime-only",
        name="Runtime Only",
        observed=True,
        status=Capability.Status.CURRENT,
    )
    client.post("/api/v1/sync", _snapshot([_cap()]), format="json")
    observed.refresh_from_db()
    assert observed.status == Capability.Status.CURRENT


@pytest.mark.django_db
def test_leftover_slug_is_remounted(client):
    client.post("/api/v1/sync", _snapshot([_cap()]), format="json")
    client.post("/api/v1/sync", _snapshot([]), format="json")
    leftover = Capability.objects.get(slug="support-agent")
    assert leftover.status == Capability.Status.LEFTOVER
    cap_id = str(leftover.id)

    remounted = client.post("/api/v1/sync", _snapshot([_cap()]), format="json")
    assert remounted.data["capabilities"][0]["id"] == cap_id
    leftover.refresh_from_db()
    assert leftover.status == Capability.Status.CURRENT


@pytest.mark.django_db
def test_archived_slug_stays_leftover(client):
    client.post("/api/v1/sync", _snapshot([_cap()]), format="json")
    client.post("/api/v1/sync", _snapshot([]), format="json")
    leftover = Capability.objects.get(slug="support-agent")
    assert leftover.status == Capability.Status.LEFTOVER

    kept = client.post("/api/v1/sync", _snapshot([_cap(archived=True)]), format="json")
    assert kept.status_code == 200
    leftover.refresh_from_db()
    assert leftover.status == Capability.Status.LEFTOVER
    assert kept.data["capabilities"][0]["archived"] is True


@pytest.mark.django_db
def test_wrong_project_id_is_403(client):
    other = str(uuid.uuid4())
    posted = client.post(
        "/api/v1/sync",
        {**_snapshot([_cap()]), "project_id": other},
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
    posted = c.post("/api/v1/sync", _snapshot([_cap()]), format="json")
    assert posted.status_code == 200, posted.data


@pytest.mark.django_db
def test_system_prompt_and_capability_card_roundtrip(client):
    card = {
        "task": "Answer product questions",
        "anchors": [{"qualname": "agent.reply", "kind": "entry_point"}],
        "trajectory_map": [
            {
                "id": "happy-path",
                "name": "Happy path",
                "claim": "code_path",
                "anchors": ["agent.reply"],
                "terminal": {"kind": "emits_record"},
            }
        ],
    }
    matrix = [
        {
            "name": "Answer quality",
            "type": "custom_judge",
            "rubric": "Score 1 if the answer is correct.",
            "measures": "correctness",
        }
    ]
    posted = client.post(
        "/api/v1/sync",
        _snapshot(
            [
                _cap(
                    system_prompt="You are helpful.",
                    capability_card=card,
                    eval_matrix=matrix,
                )
            ]
        ),
        format="json",
    )
    assert posted.status_code == 200, posted.data
    assert posted.data["capabilities"][0]["system_prompt"] == "You are helpful."
    assert posted.data["capabilities"][0]["capability_card"]["task"] == "Answer product questions"
    assert posted.data["capabilities"][0]["eval_matrix"][0]["name"] == "Answer quality"

    cap = Capability.objects.get(slug="support-agent")
    assert cap.improvement_metadata.get("system_prompt") == "You are helpful."
    assert cap.improvement_metadata["capability_card"]["trajectory_map"][0]["id"] == "happy-path"
    assert Behaviour.objects.filter(capability=cap, key="happy-path").exists()


@pytest.mark.django_db
def test_sync_enqueues_preload_on_commit(client, django_capture_on_commit_callbacks):
    card = {
        "task": "Answer product questions",
        "trajectory_map": [
            {
                "id": "happy-path",
                "name": "Happy path",
                "claim": "code_path",
                "anchors": [],
                "terminal": {"kind": "emits_record"},
            }
        ],
    }
    with (
        patch("overbae.tasks.eval.preload_capability_eval_set.delay") as delay,
        django_capture_on_commit_callbacks(execute=True),
    ):
        posted = client.post(
            "/api/v1/sync",
            _snapshot([_cap(capability_card=card)]),
            format="json",
        )
    assert posted.status_code == 200, posted.data
    cap = Capability.objects.get(slug="support-agent")
    delay.assert_called_once_with(capability_id=str(cap.id))


@pytest.mark.django_db
def test_eval_matrix_is_metadata_only_not_materialized(client, django_capture_on_commit_callbacks):
    from overbae.models import Evaluator

    matrix = [
        {
            "name": "Answer quality",
            "type": "custom_judge",
            "rubric": "Score 1 if the answer is correct.",
        }
    ]
    with (
        patch("overbae.tasks.eval.preload_capability_eval_set.delay"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        posted = client.post(
            "/api/v1/sync",
            _snapshot([_cap(eval_matrix=matrix)]),
            format="json",
        )
    assert posted.status_code == 200, posted.data
    cap = Capability.objects.get(slug="support-agent")
    assert cap.improvement_metadata["eval_matrix"][0]["name"] == "Answer quality"
    assert not Evaluator.objects.filter(capability=cap).exists()


@pytest.mark.django_db
def test_sync_normalizes_string_constraints_to_dicts(client, django_capture_on_commit_callbacks):
    constraints = [
        "Answer must be plain English",
        {"rule": "At most 5 tool calls", "type": "budget", "params": {"max_calls": 5}},
    ]
    card = {
        "task": "Answer questions",
        "trajectory_map": [
            {
                "id": "main",
                "name": "Main",
                "claim": "code_path",
                "anchors": [],
                "terminal": {"kind": "emits_record"},
            }
        ],
        "constraints": constraints,
    }
    with (
        patch("overbae.tasks.eval.preload_capability_eval_set.delay"),
        django_capture_on_commit_callbacks(execute=True),
    ):
        posted = client.post(
            "/api/v1/sync",
            _snapshot([_cap(capability_card=card)]),
            format="json",
        )
    assert posted.status_code == 200, posted.data
    cap = Capability.objects.get(slug="support-agent")
    stored = cap.improvement_metadata["capability_card"]["constraints"]
    assert stored[0] == {
        "rule": "Answer must be plain English",
        "type": "output_format",
        "params": {},
        "provenance": [],
    }
    assert stored[1]["rule"] == "At most 5 tool calls"
    assert stored[1]["type"] == "budget"
