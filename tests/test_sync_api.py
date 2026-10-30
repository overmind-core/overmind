"""API tests for POST|GET /api/v1/sync."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from rest_framework.test import APIClient

from overbae.models import (
    APIToken,
    Capability,
    Project,
    ProjectMembership,
    User,
)
from overbae.services.codebase.flow import build_capability_flow

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


@pytest.mark.django_db
def test_invalid_provenance_does_not_replace_project_snapshot(client, project):
    response = client.post(
        "/api/v1/sync",
        {**_snapshot([]), "repository_snapshot": {"commit": "not-a-sha"}},
        format="json",
    )
    assert response.status_code == 400
    project.refresh_from_db()
    assert not project.settings.get("last_synced_at")


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
def test_scan_modes_utilities_and_policy_reach_the_capability(client):
    card = {
        "task": "Write cited reports",
        "success_criteria": ["every claim cites a source"],
        "modes": [{"name": "outline", "purpose": "plan sections", "prompt": "Plan {topic}"}],
        "llm_utilities": [
            {"name": "choose_agent", "called_by": "support-agent", "cardinality": "per_run"}
        ],
    }
    posted = client.post(
        "/api/v1/sync",
        _snapshot(
            [
                _cap(
                    capability_card=card,
                    decision_logic="Outline first, then draft each section.",
                    policy_markdown="- Cite only retrieved sources.",
                )
            ]
        ),
        format="json",
    )
    assert posted.status_code == 200, posted.data
    row = posted.data["capabilities"][0]
    assert row["decision_logic"] == "Outline first, then draft each section."
    assert row["policy_markdown"] == "- Cite only retrieved sources."

    cap = Capability.objects.get(slug="support-agent")
    assert cap.decision_logic == "Outline first, then draft each section."
    assert cap.policy_markdown == "- Cite only retrieved sources."
    flow = build_capability_flow(cap)
    assert flow["modes"][0]["prompt"] == "Plan {topic}"
    assert [(u["name"], u["cardinality"]) for u in flow["llm_utilities"]] == [
        ("choose_agent", "per_run")
    ]


@pytest.mark.django_db
def test_resync_rebuilds_the_default_set_from_the_toml(client):
    matrix = [
        {"name": "Grounded", "managed_name": "faithfulness"},
        {"name": "Valid JSON", "managed_name": "json validity"},
    ]
    client.post("/api/v1/sync", _snapshot([_cap(eval_matrix=matrix)]), format="json")
    cap = Capability.objects.get(slug="support-agent")
    default = cap.active_eval_set
    assert set(default.rows.filter(capability=cap).values_list("name", flat=True)) == {"Grounded"}
    assert cap.improvement_metadata["eval_preload"]["counts"]["expectations"] == 1

    client.post("/api/v1/sync", _snapshot([_cap(eval_matrix=matrix[1:])]), format="json")

    assert not default.rows.filter(capability=cap).exists()
    assert Capability.objects.get(pk=cap.pk).active_eval_set_id == default.id


@pytest.mark.django_db
@pytest.mark.parametrize(
    "entry",
    [
        {"name": "x", "type": "deterministic", "check": "nope"},
        {"name": "x", "type": "deterministic", "check": "regex", "params": {"MISSING": 1}},
        {
            "name": "x",
            "type": "deterministic",
            "check": "schema_field_conformance",
            "params": {"TYPE_CHECKS": {}},
        },
        {"name": "x", "type": "deterministic", "check": "regex", "params": ["PATTERN"]},
    ],
)
def test_toml_deterministic_entry_with_a_bad_check_is_refused(client, entry):
    response = client.post("/api/v1/sync", _snapshot([_cap(eval_matrix=[entry])]), format="json")

    assert response.status_code == 400
    assert not Capability.objects.filter(slug="support-agent").exists()


@pytest.mark.django_db
def test_evaluation_definitions_distinguish_catalogue_code_config_and_custom(client, project):
    declared = [{"expectation": "Reports completed work accurately", "span_name": "Agent.run"}]
    matrix = [
        {
            "name": "History is present",
            "type": "deterministic",
            "check": "json_required_keys",
            "params": {"REQUIRED_KEYS": ["history"]},
            "span_name": "Agent.run",
        }
    ]
    response = client.post(
        "/api/v1/sync", _snapshot([_cap(expectations=declared, eval_matrix=matrix)]), format="json"
    )
    assert response.status_code == 200
    cap = Capability.objects.get(slug="support-agent")
    custom = client.post(
        "/api/eval-matrix/",
        {
            "project": str(project.pk),
            "name": "Shared team requirement",
            "type": "llm",
            "source": "Includes an explanation",
            "definition_source": "stock",
        },
        format="json",
    )
    assert custom.status_code == 201
    assert custom.data["definition_source"] == "custom"

    response = client.get(
        "/api/eval-matrix/", {"project": str(project.pk), "capability": str(cap.pk)}
    )
    assert response.status_code == 200
    rows = {r["name"]: r for r in response.data["results"]}
    assert rows["output_is_valid_json"]["definition_source"] == "stock"
    assert rows["json_required_keys"]["definition_source"] == "stock"
    assert rows["History is present"]["definition_source"] == "config"
    assert rows["Reports completed work accurately"]["definition_source"] == "code"
    assert rows["Shared team requirement"]["definition_source"] == "custom"

    # A familiar catalogue name alone must not establish catalogue provenance.
    row = project.eval_matrix.get(name="output_is_valid_json", capability=None)
    row.source = "def evaluate(span):\n    return True\n"
    row.save(update_fields=["source"])
    response = client.get(f"/api/eval-matrix/{row.pk}/")
    assert response.data["definition_source"] == "unknown"
