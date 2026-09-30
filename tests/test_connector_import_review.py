from datetime import timedelta

import httpx
import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from overbae.models import Capability, ConnectorCredential, Project, ProjectMembership, Span, User
from overbae.tasks import connector_sync

pytestmark = pytest.mark.django_db


@pytest.fixture
def flow(monkeypatch):
    user = User.objects.create_user(email="review@example.test", clerk_user_id="import-review")
    project = Project.objects.create(name="Review", slug="review")
    ProjectMembership.objects.create(user=user, project=project)
    client = APIClient()
    client.force_authenticate(user)
    rows = []
    requests = []

    def http_get(url, **kwargs):
        params = kwargs.get("params", {})
        requests.append((url, params))
        if url.endswith("/projects"):
            data = {"data": [{"id": "source", "name": "Customer agent"}]}
        else:
            data = {
                "data": [
                    r
                    for r in rows
                    if not params.get("traceId") or r["traceId"] == params["traceId"]
                ],
                "meta": {},
            }
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", http_get)
    monkeypatch.setattr(connector_sync.sync_connector_chunk, "apply_async", lambda **kwargs: None)
    monkeypatch.setattr(
        "overbae.services.connectors.review.enqueue_trace_scoring", lambda spans: None
    )
    response = client.post(
        "/api/connector-credentials/",
        {
            "project": str(project.id),
            "name": "Langfuse",
            "connector_type": "langfuse",
            "api_key": "pk-test",
            "api_secret": "sk-test",
        },
        format="json",
    )
    assert response.status_code == 201, response.data
    credential = ConnectorCredential.objects.get(pk=response.data["id"])
    return client, project, credential, rows, requests


def add_trace(rows, suffix, tool="search"):
    for identity, parent, name, kind, inp in [
        ("root", None, "Support agent", "AGENT", {"question": "Different values are allowed"}),
        ("tool", "root", tool, "TOOL", {"query": "a query"}),
    ]:
        rows.append(
            {
                "id": f"{suffix}-{identity}",
                "traceId": suffix,
                "parentObservationId": f"{suffix}-{parent}" if parent else None,
                "name": name,
                "type": kind,
                "input": inp,
                "output": {"answer": "done"},
                "startTime": "2026-01-02T00:00:00Z",
                "endTime": "2026-01-02T00:00:01Z",
            }
        )


def preview_and_import(flow):
    from overbae.tasks.connector_review import preview_connector_import

    client, _, credential, _, _ = flow
    url = f"/api/connector-credentials/{credential.id}/"
    response = client.post(
        url + "preview/",
        {
            "source_project_id": "source",
            "backfill_from": "2026-01-01T00:00:00Z",
            "backfill_to": "2026-01-03T00:00:00Z",
        },
        format="json",
    )
    assert response.status_code == 202, response.data
    preview_connector_import(str(response.data["id"]))
    preview = client.get(url + f"preview/?preview_id={response.data['id']}")
    assert preview.status_code == 200, preview.data
    assert preview.data["status"] == "ready", preview.data
    assert not Span.objects.filter(project=credential.project).exists()
    result = client.post(url + "import/", {"preview_id": preview.data["id"]}, format="json")
    assert result.status_code == 202, result.data
    progress = client.get(url).data
    assert progress["sync_status"] == "backfilling"
    assert progress["backfill_total"] == preview.data["trace_count"]
    if preview.data["trace_count"]:
        assert progress["import_remaining_seconds"] > 0
    credential.refresh_from_db()
    result = connector_sync.sync_connector_chunk(str(credential.id))
    while result["status"] == "backfilling":
        result = connector_sync.sync_connector_chunk(str(credential.id))
    assert result["status"] == "backfill_complete", result
    assert client.get(url).data["import_remaining_seconds"] is None
    return url, preview.data


def test_import_groups_review_and_new_arrivals(flow):
    client, project, credential, rows, requests = flow
    add_trace(rows, "a")
    add_trace(rows, "b")
    add_trace(rows, "c", "refund")
    capability = Capability.objects.create(project=project, name="Support agent", slug="support")
    url, preview = preview_and_import(flow)
    assert preview["trace_count"] == 3
    assert preview["estimated_seconds_max"] >= preview["estimated_seconds_min"] > 0
    assert Span.objects.filter(capability__isnull=False).count() == 0
    assert Span.objects.filter(parent_span_id__isnull=True).count() == 3
    assert any(p.get("fromStartTime", "").startswith("2026-01-01") for _, p in requests)
    groups = client.get(url + "groups/").data["results"]
    assert sorted(g["trace_count"] for g in groups) == [1, 2]
    group = next(g for g in groups if g["trace_count"] == 2)
    review_url = url + f"groups/{group['id']}/"
    assigned = client.patch(
        review_url,
        {"capability_id": str(capability.id), "expected_revision": group["revision"]},
        format="json",
    )
    assert assigned.status_code == 200, assigned.data
    assert Span.objects.filter(capability=capability).count() == 4
    old_revision = assigned.data["revision"]
    add_trace(rows, "d")
    credential.refresh_from_db()
    connector_sync._run_adapter_chunk(credential, {"mode": "live"})
    assert Span.objects.filter(capability=capability).count() == 6
    stale = client.patch(
        review_url,
        {"capability_id": str(capability.id), "expected_revision": old_revision},
        format="json",
    )
    assert stale.status_code == 409, stale.data
    group = next(g for g in client.get(url + "groups/").data["results"] if g["id"] == group["id"])
    assert group["unreviewed_trace_count"] == 0
    cleared = client.patch(
        review_url, {"capability_id": None, "expected_revision": group["revision"]}, format="json"
    )
    assert cleared.status_code == 200, cleared.data
    assert cleared.data["unreviewed_trace_count"] == 0
    assert not Span.objects.filter(capability__isnull=False).exists()
    assert all("overmind.capability.id" not in s.attributes for s in Span.objects.all())
    credential.refresh_from_db()
    connector_sync._run_adapter_chunk(credential, {"mode": "live"})
    assert Span.objects.count() == 8
    assert not Span.objects.filter(capability__isnull=False).exists()
    credential.refresh_from_db()
    assert credential.total_traces_imported == 4


def test_preview_must_be_ready_current_and_owned(flow):
    from overbae.models import ConnectorImportPreview

    client, project, credential, _, _ = flow
    preview = ConnectorImportPreview.objects.create(credential=credential, window_to=timezone.now())
    url = f"/api/connector-credentials/{credential.id}/import/"
    assert client.post(url, {"preview_id": str(preview.id)}, format="json").status_code == 400
    preview.status = "ready"
    preview.expires_at = timezone.now() - timedelta(seconds=1)
    preview.save()
    assert client.post(url, {"preview_id": str(preview.id)}, format="json").status_code == 400
    other = Project.objects.create(name="Other", slug="other")
    other_credential = ConnectorCredential.objects.create(
        project=other, name="Other", connector_type="langfuse"
    )
    assert (
        client.get(f"/api/connector-credentials/{other_credential.id}/groups/").status_code == 404
    )


def test_confirm_rejects_an_active_sync_lease_and_changed_credentials(flow):
    from overbae.models import ConnectorImportPreview

    client, _, credential, _, _ = flow
    preview = ConnectorImportPreview.objects.create(
        credential=credential,
        credential_updated_at=credential.updated_at,
        status="ready",
        window_to=timezone.now(),
        expires_at=timezone.now() + timedelta(minutes=10),
    )
    url = f"/api/connector-credentials/{credential.id}/import/"
    ConnectorCredential.objects.filter(pk=credential.pk).update(
        sync_lease_expires_at=timezone.now() + timedelta(minutes=5)
    )
    assert client.post(url, {"preview_id": str(preview.id)}, format="json").status_code == 400
    ConnectorCredential.objects.filter(pk=credential.pk).update(sync_lease_expires_at=None)
    credential.base_url = "https://changed.example.test"
    credential.save()
    assert client.post(url, {"preview_id": str(preview.id)}, format="json").status_code == 400


def test_review_rejects_foreign_capability_and_clears_obsolete_scoring(flow):
    from overbae.models import ScoringPass, TaskExecution, Verdict

    client, project, _, rows, _ = flow
    add_trace(rows, "scored")
    url, _ = preview_and_import(flow)
    group = client.get(url + "groups/").data["results"][0]
    review_url = url + f"groups/{group['id']}/"
    other = Project.objects.create(name="Other", slug="other-capability")
    foreign = Capability.objects.create(project=other, name="Foreign", slug="foreign")
    result = client.patch(
        review_url,
        {"capability_id": str(foreign.id), "expected_revision": group["revision"]},
        format="json",
    )
    assert result.status_code == 400
    root = Span.objects.get(project=project, parent_span_id=None)
    TaskExecution.objects.create(project=project, trace_id=root.trace_id, unit_span_id=root.span_id)
    ScoringPass.objects.create(project=project, trace_id=root.trace_id)
    Verdict.objects.create(
        project=project,
        target_kind="trace",
        target_id=root.trace_id,
        evaluator_name="Old judge",
        score=0.9,
    )
    result = client.patch(
        review_url, {"capability_id": None, "expected_revision": group["revision"]}, format="json"
    )
    assert result.status_code == 200, result.data
    assert not TaskExecution.objects.filter(project=project).exists()
    assert not ScoringPass.objects.filter(project=project).exists()
    assert not Verdict.objects.filter(project=project).exists()


def test_reimport_repairs_old_trace_partition_without_replacing_human_assignment(flow):
    client, project, credential, rows, _ = flow
    add_trace(rows, "reimport")
    for row in rows:
        row["updatedAt"] = "2026-01-02T00:00:02Z"
    url, _ = preview_and_import(flow)
    original_trace = Span.objects.get(project=project, parent_span_id=None).trace_id
    group = client.get(url + "groups/").data["results"][0]
    capability = Capability.objects.create(project=project, name="Support", slug="support")
    result = client.patch(
        url + f"groups/{group['id']}/",
        {"capability_id": str(capability.id), "expected_revision": group["revision"]},
        format="json",
    )
    assert result.status_code == 200
    Span.objects.filter(project=project, parent_span_id__isnull=False).update(
        trace_id="f" * 32, parent_span_id=None
    )
    credential.refresh_from_db()
    connector_sync._run_adapter_chunk(credential, {"mode": "live"})
    assert set(Span.objects.filter(project=project).values_list("trace_id", flat=True)) == {
        original_trace
    }
    assert Span.objects.filter(project=project, parent_span_id=None).count() == 1
    assert Span.objects.filter(project=project, capability=capability).count() == 2
    assert client.get(url + "groups/").data["results"][0]["unreviewed_trace_count"] == 0


def test_provider_content_update_reuses_the_approved_pattern(flow):
    client, project, credential, rows, _ = flow
    add_trace(rows, "updated")
    url, _ = preview_and_import(flow)
    group = client.get(url + "groups/").data["results"][0]
    capability = Capability.objects.create(project=project, name="Support", slug="support")
    result = client.patch(
        url + f"groups/{group['id']}/",
        {"capability_id": str(capability.id), "expected_revision": group["revision"]},
        format="json",
    )
    assert result.status_code == 200
    rows[0]["output"] = {"answer": "Completed later"}
    credential.refresh_from_db()
    connector_sync._run_adapter_chunk(credential, {"mode": "live"})
    updated = client.get(url + "groups/").data["results"][0]
    assert updated["unreviewed_trace_count"] == 0
    assert updated["revision"] > result.data["revision"]
    assert Span.objects.filter(project=project, capability=capability).count() == 2


def test_invalid_group_id_is_a_validation_error(flow):
    client, _, credential, _, _ = flow
    result = client.patch(
        f"/api/connector-credentials/{credential.id}/groups/invalid/",
        {"capability_id": None, "expected_revision": 1},
        format="json",
    )
    assert result.status_code == 400


def test_batch_review_is_atomic_and_rules_cover_future_arrivals(flow):
    client, project, credential, rows, _ = flow
    add_trace(rows, "assigned")
    add_trace(rows, "unassigned", "refund")
    url, _ = preview_and_import(flow)
    capability = Capability.objects.create(project=project, name="Answer", slug="answer")
    groups = client.get(url + "groups/").data["results"]
    choices = [
        {
            "group_id": g["id"],
            "capability_id": str(capability.id) if "search" in g["evidence"]["tools"] else None,
            "expected_revision": g["revision"],
        }
        for g in groups
    ]
    choices.sort(key=lambda choice: choice["group_id"])
    choices[-1]["expected_revision"] += 1
    rejected = client.post(url + "review/", {"assignments": choices}, format="json")
    assert rejected.status_code == 409, rejected.data
    assert not Span.objects.filter(connector_reviewed=True).exists()
    assert not credential.trace_groups.filter(reviews__isnull=False).exists()
    choices[-1]["expected_revision"] -= 1
    accepted = client.post(url + "review/", {"assignments": choices}, format="json")
    assert accepted.status_code == 200, accepted.data
    assert client.get(url).data["review_summary"]["pending_groups"] == 0
    add_trace(rows, "matched")
    add_trace(rows, "matched-unassigned", "refund")
    add_trace(rows, "novel", "escalate")
    credential.refresh_from_db()
    connector_sync._run_adapter_chunk(credential, {"mode": "live"})
    assert Span.objects.filter(capability=capability).count() == 4
    assert Span.objects.filter(connector_reviewed=True, capability=None).count() == 4
    assert Span.objects.filter(connector_reviewed=False, capability=None).count() == 2
    summary = client.get(url).data["review_summary"]
    assert summary == {"pending_groups": 1, "pending_traces": 1}
    capability.status = "leftover"
    capability.save(update_fields=["status"])
    assert client.get(url).data["review_summary"]["pending_groups"] == 2
    assert len(client.get(url + "groups/?pending_only=true").data["results"]) == 2
    assert all(
        g["needs_review"] for g in client.get(url + "groups/?pending_only=true").data["results"]
    )
    add_trace(rows, "retired")
    credential.refresh_from_db()
    connector_sync._run_adapter_chunk(credential, {"mode": "live"})
    assert Span.objects.filter(capability=capability).count() == 4
    assert client.get(url).data["review_summary"]["pending_groups"] == 2


def test_structure_change_does_not_inherit_an_approved_rule(flow):
    client, project, credential, rows, _ = flow
    add_trace(rows, "changing")
    url, _ = preview_and_import(flow)
    capability = Capability.objects.create(project=project, name="Answer", slug="answer")
    group = client.get(url + "groups/").data["results"][0]
    assert (
        client.post(
            url + "review/",
            {
                "assignments": [
                    {
                        "group_id": group["id"],
                        "capability_id": str(capability.id),
                        "expected_revision": group["revision"],
                    }
                ]
            },
            format="json",
        ).status_code
        == 200
    )
    rows[1]["name"] = "refund"
    credential.refresh_from_db()
    connector_sync._run_adapter_chunk(credential, {"mode": "live"})
    assert not Span.objects.filter(capability__isnull=False).exists()
    assert client.get(url).data["review_summary"]["pending_groups"] == 1


def test_empty_six_month_preview_does_not_walk_daily_windows(flow):
    from overbae.tasks.connector_review import preview_connector_import

    client, _, credential, _, requests = flow
    result = client.post(
        f"/api/connector-credentials/{credential.id}/preview/",
        {
            "source_project_id": "source",
            "backfill_from": "2026-03-01T00:00:00Z",
            "backfill_to": "2026-09-01T00:00:00Z",
        },
        format="json",
    )
    preview_connector_import(result.data["id"])
    result = client.get(
        f"/api/connector-credentials/{credential.id}/preview/?preview_id={result.data['id']}"
    )
    assert result.data["status"] == "ready"
    assert result.data["trace_count"] == 0
    ranged = [
        params for _, params in requests if params.get("fromStartTime", "").startswith("2026-03-01")
    ]
    assert len(ranged) == 1
    assert ranged[0]["fields"] == "core"


def test_preview_counts_cursor_pages_without_fetching_trace_payloads(flow, monkeypatch):
    from overbae.models import ConnectorImportPreview
    from overbae.tasks.connector_review import preview_connector_import

    client, _, credential, _, _ = flow
    calls = []

    def get(url, **kwargs):
        params = kwargs.get("params", {})
        assert "traceId" not in params
        if params.get("limit") == 1:
            data = {"data": []}
        elif params.get("cursor") == "next":
            progress = ConnectorImportPreview.objects.get(credential=credential)
            assert progress.trace_count == 1
            data = {"data": [{"id": "c", "traceId": "two"}], "meta": {}}
        else:
            data = {
                "data": [{"id": "a", "traceId": "one"}, {"id": "b", "traceId": "one"}],
                "meta": {"cursor": "next"},
            }
        calls.append(params)
        return httpx.Response(200, json=data, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)
    response = client.post(
        f"/api/connector-credentials/{credential.id}/preview/",
        {"source_project_id": "source"},
        format="json",
    )
    preview_connector_import(response.data["id"])
    result = ConnectorImportPreview.objects.get(pk=response.data["id"])
    assert result.status == "ready"
    assert result.trace_count == 2
    assert result.span_count == 3
    assert len(calls) <= 3


def test_orphaned_preview_returns_a_retryable_failure(flow):
    from overbae.models import ConnectorImportPreview

    client, _, credential, _, _ = flow
    preview = ConnectorImportPreview.objects.create(
        credential=credential, window_to=timezone.now(), status="running"
    )
    ConnectorImportPreview.objects.filter(pk=preview.pk).update(
        created_at=timezone.now() - timedelta(minutes=4)
    )
    result = client.get(
        f"/api/connector-credentials/{credential.id}/preview/?preview_id={preview.id}"
    )
    assert result.data["status"] == "failed"
    assert "again" in result.data["error"]


def test_rate_limited_preview_fails_promptly_with_retry_guidance(flow, monkeypatch):
    from overbae.models import ConnectorImportPreview
    from overbae.tasks.connector_review import preview_connector_import

    client, _, credential, _, _ = flow
    ConnectorCredential.objects.filter(pk=credential.pk).update(api_version="v2")
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        return httpx.Response(
            429, json={}, headers={"Retry-After": "30"}, request=httpx.Request("GET", url)
        )

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(
        "overbae.services.connectors.langfuse.client.time.sleep",
        lambda _: (_ for _ in ()).throw(AssertionError("Count must not sleep on rate limits")),
    )
    response = client.post(
        f"/api/connector-credentials/{credential.id}/preview/",
        {"source_project_id": "source"},
        format="json",
    )
    preview_connector_import(response.data["id"])
    result = ConnectorImportPreview.objects.get(pk=response.data["id"])
    assert result.status == "failed"
    assert "rate limit" in result.error.lower()
    assert len(calls) == 1
