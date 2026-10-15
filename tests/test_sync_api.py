"""API tests for POST|GET /api/v1/sync."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
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
        **_snapshot([_cap()]),
        "repository_snapshot": source,
        "last_synced_at": "2000-01-01T00:00:00Z",
    }
    now = datetime(2026, 9, 27, 12, tzinfo=UTC)
    with patch("overbae.services.sync.timezone.now", return_value=now):
        response = client.post("/api/v1/sync", body, format="json")
    assert response.status_code == 200, response.data
    assert response.data["repository_snapshot"] == source
    assert response.data["last_synced_at"] == "2026-09-27T12:00:00+00:00"
    pulled = client.get("/api/v1/sync", {"project_id": str(project.id)}).data
    assert pulled["repository_snapshot"] == source
    graph = client.get("/api/agent/", {"project": str(project.id)}).data
    assert graph["repository_snapshot"] == source
    assert graph["last_synced_at"] == response.data["last_synced_at"]
    response = client.post("/api/v1/sync", _snapshot([_cap()]), format="json")
    assert response.data["repository_snapshot"] is None


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
    assert cap.active_eval_set.name == "Default"
    assert cap.active_eval_set.rows.get(name="Answer quality").source == "correctness"


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
def test_toml_deterministic_entry_becomes_a_parameterised_row_in_the_default_set(client):
    matrix = [
        {
            "name": "Reply carries ticket keys",
            "type": "deterministic",
            "check": "json_required_keys",
            "params": {"REQUIRED_KEYS": ["ticket_id", "status"]},
            "span_name": "SupportAgent.run",
        }
    ]
    client.post("/api/v1/sync", _snapshot([_cap(eval_matrix=matrix)]), format="json")

    cap = Capability.objects.get(slug="support-agent")
    row = cap.active_eval_set.rows.get(name="Reply carries ticket keys")
    assert row.type == "deterministic"
    assert row.span_name == "SupportAgent.run"
    assert "REQUIRED_KEYS = ['ticket_id', 'status']" in row.source
    assert "def evaluate(span)" in row.source
    assert cap.improvement_metadata["eval_preload"]["counts"]["expectations"] == 0

    matrix[0]["params"] = {"REQUIRED_KEYS": ["ticket_id"]}
    client.post("/api/v1/sync", _snapshot([_cap(eval_matrix=matrix)]), format="json")
    assert (
        "REQUIRED_KEYS = ['ticket_id']"
        in cap.active_eval_set.rows.get(name="Reply carries ticket keys").source
    )


@pytest.mark.django_db
def test_code_expectations_become_span_scoped_rows_and_leave_with_the_code(client):
    declared = [
        {"expectation": "Cites the order id", "span_name": "SupportAgent.run"},
        {"expectation": "Lists the refund steps", "span_name": "summarize"},
    ]
    client.post("/api/v1/sync", _snapshot([_cap(expectations=declared)]), format="json")

    cap = Capability.objects.get(slug="support-agent")
    rows = {row.name: row for row in cap.active_eval_set.rows.filter(capability=cap)}
    assert {name: (row.type, row.source, row.span_name) for name, row in rows.items()} == {
        "Cites the order id": ("llm", "Cites the order id", "SupportAgent.run"),
        "Lists the refund steps": ("llm", "Lists the refund steps", "summarize"),
    }
    assert cap.improvement_metadata["eval_preload"]["counts"]["expectations"] == 2

    client.post("/api/v1/sync", _snapshot([_cap(expectations=declared[:1])]), format="json")
    assert set(cap.eval_matrix.values_list("name", flat=True)) == {"Cites the order id"}

    client.post("/api/v1/sync", _snapshot([_cap()]), format="json")
    assert set(cap.eval_matrix.values_list("name", flat=True)) == {"Cites the order id"}


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
def test_sync_normalizes_string_constraints_to_dicts(client):
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


@pytest.mark.django_db
def test_sync_retains_tool_branch_conditions_and_local_outcomes(client):
    branch = {
        "condition": "The clicked element is a select input",
        "basis": "code",
        "action": "Read dropdown options",
        "outcome": "Available choices are returned",
        "next": "Return choices to the next decision",
        "transitions": [{"target": "Agent.decide", "condition": "Action returns choices"}],
        "provenance": ["tools.py#L20-L35"],
    }
    response = client.post(
        "/api/v1/sync",
        _snapshot(
            [
                _cap(
                    capability_card={
                        "task": "Operate the browser",
                        "tool_spec": [
                            {
                                "name": "click",
                                "purpose": "Activate an element",
                                "args": "index",
                                "branches": [branch],
                            }
                        ],
                    }
                )
            ]
        ),
        format="json",
    )
    assert response.status_code == 200, response.content
    capability = Capability.objects.get(project_id=PROJECT_ID, slug="support-agent")
    detail = client.get(f"/api/capabilities/{capability.id}/")
    assert detail.status_code == 200, detail.content
    assert detail.json()["flow"]["tool_spec"][0]["branches"] == [branch]
    assert capability.improvement_metadata["capability_card"]["tool_spec"][0]["branches"] == [
        branch
    ]
