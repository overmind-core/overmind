import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.utils import timezone
from starlette.testclient import TestClient
from test_data_first_workflow import workspace
from test_mcp_research_journey import call

from modal_shared.operational_events import Journal
from overbae.models import APIToken, FinetuningJob, Project, ProjectMembership, User
from overbae.services import operational_progress, provider_progress
from overbae.services.finetuning_runner import PollSnapshot
from overbae.services.mcp.server import create_mcp_application
from overbae.services.provider_progress import read_page as read_provider_page
from overbae.tasks.finetuning import observe_finetuning_job
from overbae.tasks.finetuning_reconciler import reconcile_finetuning_jobs

pytestmark = pytest.mark.django_db(transaction=True)
emit_provider_event = Journal.emit


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
@pytest.mark.parametrize("lost_transition", [False, True])
def test_training_terminal_timeline_matches_mcp_without_another_provider_call(
    terminal, lost_transition
):
    project, _, dataset = workspace()
    user = project.memberships.select_related("user").first().user
    heartbeat = timezone.now() - timedelta(seconds=10)
    progress = {
        "stage": "verifying_checkpoint",
        "trained_steps": 21,
        "total_steps": 21,
        "diagnostics": {"heartbeat_at": heartbeat.isoformat(), "completed": None, "total": None},
    }
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-0.6B",
        status="running",
        provider="modal",
        remote_job_id="receipt:fc-test",
        started_at=heartbeat - timedelta(minutes=2),
        hyperparameters={"objective": "decision_cross_entropy"},
        progress=progress,
    )
    operational_progress.training(job)
    runner = Mock()
    runner.poll.return_value = PollSnapshot(
        state=terminal, step=21, trained_steps=21, total_steps=21
    )
    runner.is_terminal_ok.side_effect = lambda state: state == "succeeded"
    runner.is_terminal_fail.side_effect = lambda state: state == "failed"
    runner.is_terminal_cancelled.side_effect = lambda state: state == "cancelled"
    runner.fetch_epoch_losses.return_value = []
    with (
        patch("overbae.services.finetuning_runner.get_runner", return_value=runner),
        patch("overbae.tasks.finetuning._charge_modal_finetuning"),
        patch("overbae.celery.get_celery_app") as app,
    ):
        inventory = app.return_value.control.inspect.return_value
        for method in ("active", "reserved", "scheduled"):
            getattr(inventory, method).return_value = {"worker": []}
        if lost_transition:
            FinetuningJob.objects.filter(pk=job.pk).update(
                status=terminal, completed_at=timezone.now()
            )
        else:
            observe_finetuning_job(job)
        calls = runner.poll.call_count
        reconcile_finetuning_jobs()
        reconcile_finetuning_jobs()
        assert runner.poll.call_count == calls
        runner.submit.assert_not_called()
    job.refresh_from_db()
    key, _ = APIToken.create_for_user(user, project=project)
    with TestClient(create_mcp_application()) as client:
        result = call(client, key, "get_job", {"kind": "finetune_job", "id": str(job.pk)})
        operation = result["details"]["operation"]
        assert result["status"] == operation["status"] == terminal
        assert operation["stage"] == terminal
        assert operation["completed"] == operation["total"] == 21
        assert operation["unit"] == "steps"
        events = call(client, key, "inspect_operation", {"operation": operation["id"]})
        terminal_events = [e for e in events["operation"]["events"] if e["status"] == terminal]
        assert len(terminal_events) == 1
        assert terminal_events[0]["source_at"] == job.completed_at.isoformat()
        assert operation["last_heartbeat_at"] == heartbeat.isoformat()


def test_timeline_preserves_progress_across_heartbeats_and_pagination():
    project = Project.objects.create(name="Operations", slug="operations")
    started = timezone.now() - timedelta(minutes=5)
    run = operational_progress.record(
        project.pk,
        "deployment",
        "model",
        "attempt",
        stage="loading_weights",
        completed=1,
        total=3,
        unit="shards",
        source_at=started,
    )
    operational_progress.record(
        project.pk,
        "deployment",
        "model",
        "attempt",
        stage="loading_weights",
        completed=1,
        total=3,
        unit="shards",
        heartbeat_at=timezone.now(),
    )
    snapshot = operational_progress.inspect(project.pk, run.pk, limit=1)
    assert snapshot["last_progress_at"] == started.isoformat()
    assert snapshot["last_heartbeat_at"] is not None
    assert snapshot["page"]["has_more"]
    cursor = snapshot["page"]["next_cursor"]
    remaining = operational_progress.inspect(project.pk, run.pk, after=cursor)
    assert len(remaining["events"]) == 1
    assert remaining["events"][0]["event"] == "heartbeat"
    assert not remaining["page"]["has_more"]
    with pytest.raises(operational_progress.OperationNotFoundError):
        operational_progress.inspect(Project.objects.create(name="Other", slug="other").pk, run.pk)


def test_provider_event_replays_do_not_duplicate_or_regress_the_timeline():
    project = Project.objects.create(name="Operations", slug="operations")
    now = timezone.now()
    first = dict(stage="loading_weights", completed=1, total=3, unit="shards", source_at=now)
    run = operational_progress.record(project.pk, "deployment", "model", "attempt", **first)
    operational_progress.record(project.pk, "deployment", "model", "attempt", **first)
    operational_progress.record(
        project.pk,
        "deployment",
        "model",
        "attempt",
        stage="ready",
        status="succeeded",
        source_at=now + timedelta(seconds=1),
    )
    operational_progress.record(project.pk, "deployment", "model", "attempt", **first)
    result = operational_progress.inspect(project.pk, run.pk)
    assert result["stage"] == "ready"
    assert len(result["events"]) == 2
    assert result["completed"] is None
    assert result["last_heartbeat_at"] is None


def test_diagnostics_reject_unstructured_provider_secrets():
    project = Project.objects.create(name="Operations", slug="operations")
    with pytest.raises(ValueError):
        operational_progress.record(
            project.pk,
            "deployment",
            "model",
            "attempt",
            stage="loading",
            facts={"api_key": "not-for-the-agent"},
        )
    assert "not-for-the-agent" not in json.dumps(
        operational_progress.latest(project.pk, "deployment", "model")
    )


def test_mcp_operation_drilldown_is_passive_paginated_and_project_scoped():
    user = User.objects.create_user(email="operations@test.com", password="pw")
    project = Project.objects.create(name="Operations", slug="operations")
    ProjectMembership.objects.create(user=user, project=project)
    key, _ = APIToken.create_for_user(user, project=project)
    run = operational_progress.record(project.pk, "deployment", "model", "attempt", stage="waiting")
    operational_progress.record(project.pk, "deployment", "model", "attempt", stage="loading")
    with TestClient(create_mcp_application()) as client:
        response = client.post(
            "/api/mcp/",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "inspect_operation",
                    "arguments": {"operation": str(run.pk), "limit": 1},
                },
            },
            headers={"X-Api-Key": key, "Accept": "application/json"},
        )
    result = response.json()["result"]
    assert not result.get("isError"), result
    assert result["structuredContent"]["operation"]["page"]["has_more"]
    assert result["structuredContent"]["operation"]["events"][0]["stage"] == "waiting"


def test_provider_events_survive_observer_restart_and_do_not_leak_extra_fields(monkeypatch):
    project = Project.objects.create(name="Provider", slug="provider")
    run = operational_progress.record(project.pk, "deployment", "model", "attempt", stage="warming")
    run.provider_state = {"call": {"available": False, "reason": "not_reported"}}
    run.save(update_fields=["provider_state"])

    async def page(environment, stream, cursor):
        if stream.startswith("worker-call:"):
            return None, []
        return {"writer": "worker-a", "sequence": 1, "available_through": 1}, [] if cursor else [
            {
                "stage": "restoring_weights",
                "completed": 1024,
                "total": 2048,
                "unit": "bytes",
                "source_at": timezone.now().timestamp(),
                "facts": {"parameters": 2},
                "raw_provider_log": "secret diagnostic",
            }
        ]

    monkeypatch.setattr(provider_progress, "read_page", page)
    provider_progress.collect(run, environment="test", call_id="fc-test")
    run.refresh_from_db()
    provider_progress.collect(run, environment="test", call_id="fc-test")
    result = operational_progress.inspect(project.pk, run.pk)
    events = [event for event in result["events"] if event["event"] == "provider"]
    assert len(events) == 1
    assert events[0]["completed"] == 1024
    assert "secret diagnostic" not in json.dumps(result)
    assert result["provider"]["request"]["available"] is False
    assert result["provider"]["call"].get("reason") is None


@pytest.mark.asyncio
async def test_provider_worker_restart_retains_uncollected_tail(monkeypatch):
    old = {"writer": "old", "sequence": 3, "previous": None}
    new = {"writer": "new", "sequence": 1, "previous": "old"}
    values = {
        "pool": new,
        "new:head": new,
        "old:head": old,
        "old:3": {"stage": "engine_ready"},
        "new:1": {"stage": "restoring_weights"},
    }

    async def get(key, default=None):
        return values.get(key, default)

    monkeypatch.setattr(
        provider_progress.modal.Dict,
        "from_name",
        lambda *args, **kwargs: SimpleNamespace(get=SimpleNamespace(aio=get)),
    )
    head, events = await read_provider_page("test", "pool", {"writer": "old", "sequence": 2})
    assert head["writer"] == "old"
    assert events == [{"stage": "engine_ready"}]
    head, events = await read_provider_page("test", "pool", head)
    assert head["writer"] == "new"
    assert events == [{"stage": "restoring_weights"}]


def test_lost_journal_ack_does_not_overwrite_events_or_create_self_ancestry(monkeypatch):
    values = {}
    fail_ack = True

    async def get(key, default=None):
        return values.get(key, default)

    async def put(key, value):
        nonlocal fail_ack
        values[key] = value
        if key == "pool" and fail_ack:
            fail_ack = False
            raise TimeoutError

    monkeypatch.setattr(
        provider_progress.modal.Dict,
        "from_name",
        lambda *a, **kw: SimpleNamespace(
            get=SimpleNamespace(aio=get), put=SimpleNamespace(aio=put)
        ),
    )
    journal = Journal("pool")
    emit_provider_event(journal, "loading_weights")
    emit_provider_event(journal, "engine_ready")
    assert values["pool"]["previous"] is None
    assert values[f"{journal.writer}:1"]["stage"] == "loading_weights"
    assert values[f"{journal.writer}:2"]["stage"] == "engine_ready"
    assert values["pool"]["dropped"] == 0
    assert values["pool"]["publication_failures"] == 1


def test_decreased_work_count_and_expanded_total_do_not_reset_forward_progress_clock():
    project = Project.objects.create(name="Counters", slug="counters")
    now = timezone.now()
    run = operational_progress.record(
        project.pk,
        "training_preparation",
        "prep",
        "attempt",
        stage="tokenizing",
        completed=8,
        total=10,
        unit="rows",
        source_at=now,
    )
    operational_progress.record(
        project.pk,
        "training_preparation",
        "prep",
        "attempt",
        stage="tokenizing",
        completed=7,
        total=12,
        unit="rows",
        source_at=now + timedelta(seconds=1),
    )
    run.refresh_from_db()
    assert run.last_progress_at == now
    assert run.events.last().event == "observation"


def test_exported_rows_do_not_claim_upload_completion():
    project = Project.objects.create(name="Upload facts", slug="upload-facts")
    prep = SimpleNamespace(
        pk="prep",
        cell=SimpleNamespace(dataset=SimpleNamespace(project_id=project.pk)),
        deadline=timezone.now(),
        state="starting",
        remote_id="",
    )
    run = operational_progress.preparation(
        prep, {"stage": "uploading", "completed_rows": 10000, "total_rows": 10000}
    )
    assert run.snapshot["completed"] is None
    assert run.snapshot["total"] is None
    assert run.snapshot["facts"]["source_export_rows"] == 10000


def test_terminal_operation_keeps_durable_collection_work_until_backlog_is_drained(monkeypatch):
    project = Project.objects.create(name="Backlog", slug="backlog")
    run = operational_progress.record(
        project.pk, "inference_request", "request", "attempt", stage="succeeded", status="succeeded"
    )

    async def page(environment, stream, cursor):
        if stream.startswith("worker-call:"):
            return None, []
        sequence = cursor.get("sequence", 0) + 1
        return {"writer": "worker", "sequence": sequence, "has_more": sequence < 2}, [
            {
                "stage": "response_received",
                "source_at": timezone.now().timestamp(),
                "facts": {},
            }
        ]

    monkeypatch.setattr(provider_progress, "read_page", page)
    provider_progress.collect(run, environment="test", call_id="fc-backlog")
    run.refresh_from_db()
    assert run.next_collection_at is not None
    assert run.collection["call_id"] == "fc-backlog"
    provider_progress.collect(run, environment="test", call_id="fc-backlog")
    run.refresh_from_db()
    assert run.next_collection_at is None
    assert run.events.filter(event="provider").count() == 2
