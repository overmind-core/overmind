import asyncio
import json
import re
from pathlib import Path

import pytest
from mcp.shared.exceptions import McpError
from mcp_fixtures import mcp_context
from rest_framework.test import APIClient

from overbae.models import (
    Activity,
    Cell,
    Dataset,
    DatasetPipelineRun,
    FinetuningJob,
    ProjectMembership,
)
from overbae.services.mcp.catalog import CATALOG
from overbae.services.mcp.context import bind_context
from overbae.services.mcp.resources import read_resource

pytestmark = pytest.mark.django_db(transaction=True)


def call(context, tool, **arguments):
    result = asyncio.run(CATALOG.call(tool, arguments, context))
    assert not result.isError, result.content
    return result.structuredContent


def read_activity(context, project_id=None):
    with bind_context(context):
        uri = "overmind://activity" + (f"?project_id={project_id}" if project_id else "")
        return json.loads(asyncio.run(read_resource(uri))[0].content)


def client_for(context):
    client = APIClient()
    client.force_authenticate(context.user, context.token)
    return client


def test_mcp_mutations_share_activity_with_console_without_logging_inspections():
    context = mcp_context(permission=["read", "write"], client_name="codex")
    call(context, "list_datasets")
    before = Activity.objects.count()
    for _ in range(3):
        read_activity(context)
        call(context, "list_datasets")
    assert Activity.objects.count() == before

    created = call(
        context,
        "create_eval_matrix_row",
        name="Grounded answer",
        type="llm",
        source="Do not include this criterion in notifications: secret-evidence",
    )
    project = str(context.project.pk)
    client = client_for(context)
    feed = client.get("/api/activity/", {"project": project})
    assert feed.status_code == 200, feed.data
    event = next(item for item in feed.data["results"] if item["kind"] == "evaluation_added")
    assert event["actor_kind"] == "codex"
    assert event["actor_name"] == "Codex"
    assert event["assets"][0]["id"] == created["row"]["id"]
    assert "secret-evidence" not in str(feed.data)
    assert next(
        step for step in read_activity(context)["milestones"] if step["kind"] == "agent_connected"
    )["completed_at"]
    assert client.get("/api/activity/", {"project": project, "limit": 1}).data["next_offset"] == 1

    call(context, "delete_eval_matrix_row", row=created["row"]["id"])
    after = read_activity(context)
    assert any(item["action"] == "Deleted an evaluation" for item in after["results"])
    rejected = asyncio.run(CATALOG.call("create_eval_matrix_row", {"name": "Invalid"}, context))
    assert rejected.isError
    assert read_activity(context)["count"] == after["count"]


def test_activity_read_state_is_per_user_scoped_and_cannot_acknowledge_future_changes():
    context = mcp_context(permission=["read", "write"])
    peer = mcp_context(permission=["read", "write"])
    ProjectMembership.objects.create(user=peer.user, project=context.project)
    call(context, "create_eval_matrix_row", name="First", type="llm", source="First criterion")
    project = str(context.project.pk)
    client = client_for(context)
    first = client.get("/api/activity/", {"project": project}).data
    ids = [item["id"] for item in first["results"]]
    seen = client.post(
        "/api/activity/mark-seen/",
        {"project": project, "ids": ids, "through": first["observed_at"]},
        format="json",
    )
    assert seen.status_code == 200, seen.data
    assert client.get("/api/activity/", {"project": project}).data["unseen_count"] == 0

    peer_client = APIClient()
    peer_client.force_authenticate(peer.user)
    assert peer_client.get("/api/activity/", {"project": project}).data["unseen_count"] == len(ids)
    assert client.get("/api/activity/", {"project": str(peer.project.pk)}).status_code == 403
    with pytest.raises(McpError):
        read_activity(context, str(peer.project.pk))

    call(context, "create_eval_matrix_row", name="Second", type="llm", source="Second criterion")
    current = client.get("/api/activity/", {"project": project}).data
    client.post(
        "/api/activity/mark-seen/",
        {
            "project": project,
            "ids": [item["id"] for item in current["results"]],
            "through": first["observed_at"],
        },
        format="json",
    )
    assert client.get("/api/activity/", {"project": project}).data["unseen_count"] == 1


def test_sync_milestones_and_training_cancellation_are_deduplicated_outcomes():
    context = mcp_context(permission=["read", "write"], client_name="claude-code")
    call(context, "list_datasets")
    client = client_for(context)
    project = str(context.project.pk)
    snapshot = {
        "project_id": project,
        "version": "1",
        "capabilities": [{"slug": "support", "name": "Support", "entrypoint_fn": "support.run"}],
    }
    for _ in range(2):
        synced = client.post("/api/v1/sync", snapshot, format="json")
        assert synced.status_code == 200, synced.data
    progress = read_activity(context)["milestones"]
    assert [step["kind"] for step in progress if step["completed_at"]] == [
        "agent_connected",
        "repository_synced",
    ]
    assert Activity.objects.filter(project=context.project, kind="repository_synced").count() == 1

    dataset = Dataset.objects.create(project=context.project, name="Cancellation fixture")
    job = FinetuningJob.objects.create(
        project=context.project, dataset=dataset, status="awaiting_launch"
    )
    for _ in range(2):
        cancelled = call(context, "cancel_finetune", job=str(job.pk))
        assert cancelled["status"] == "cancelled"
    events = read_activity(context)["results"]
    cancellations = [event for event in events if event["kind"] == "task_cancelled"]
    assert len(cancellations) == 1
    assert cancellations[0]["actor_name"] == "Claude Code"
    assert cancellations[0]["status"] == "cancelled"
    assert cancellations[0]["task_id"] == str(job.pk)
    assert cancellations[0]["assets"][0]["id"] == str(dataset.pk)


def test_reported_onboarding_uses_current_roadmap_without_notification_noise():
    from uuid import uuid4

    context = mcp_context(permission=["read", "write"], client_name="codex")
    call(context, "list_datasets")
    before = read_activity(context)["count"]
    run_id = str(uuid4())
    for count in (2, 5, 5):
        result = call(context, "report_onboarding_progress", run_id=run_id, completed_steps=count)
    labels = [step["label"] for step in result["milestones"]]
    assert labels == [
        "Install Overmind",
        "Connect project",
        "Inspect repository",
        "Discover capabilities",
        "Describe capabilities",
        "Verify source references",
        "Write snapshot",
        "Validate configuration",
        "Sync capabilities",
        "Instrument capabilities",
    ]
    roadmap = (
        Path(__file__).resolve().parents[1]
        / "overmind/skills/overmind/references/onboarding-progress.md"
    )
    assert labels == re.findall(r"^1\. \*\*(.*?)\*\*", roadmap.read_text(), re.MULTILINE)
    assert sum(bool(step["completed_at"]) for step in result["milestones"]) == 5
    assert read_activity(context)["count"] == before
    client = client_for(context)
    progress = client.get("/api/activity/onboarding/", {"project": str(context.project.pk)}).data
    assert [step["label"] for step in progress] == labels
    assert sum(bool(step["completed_at"]) for step in progress) == 5
    assert {(step["actor_kind"], step["actor_name"]) for step in progress} == {("codex", "Codex")}
    # Older reports cannot regress a running attempt; a new attempt starts independently.
    call(context, "report_onboarding_progress", run_id=run_id, completed_steps=2)
    assert sum(bool(step["completed_at"]) for step in read_activity(context)["milestones"]) == 5
    reset = call(context, "report_onboarding_progress", run_id=str(uuid4()), completed_steps=2)
    assert sum(bool(step["completed_at"]) for step in reset["milestones"]) == 2
    for count in (-1, 11):
        rejected = asyncio.run(
            CATALOG.call(
                "report_onboarding_progress", {"run_id": run_id, "completed_steps": count}, context
            )
        )
        assert rejected.isError


def test_dataset_creation_is_an_asset_action_not_a_source_import_job():
    context = mcp_context(permission=["read", "write"], client_name="codex")
    result = call(
        context, "start_dataset", name="Passenger review", brief="Review passenger records"
    )
    event = Activity.objects.get(project=context.project, action="Created")
    assert event.actor_name == "Codex"
    assert event.assets == [
        {"kind": "dataset", "id": result["dataset"]["id"], "label": "Passenger review"}
    ]
    assert event.task_id == ""


@pytest.mark.parametrize(
    "mode,action",
    [
        ("preview", "Requested a transformation preview for"),
        ("publish", "Requested a transformation of"),
    ],
)
def test_transformation_activity_uses_recorded_mode(mode, action):
    from types import SimpleNamespace
    from uuid import uuid4

    from overbae.services.activity import mcp_succeeded

    context = mcp_context(permission=["read", "write"], client_name="codex")
    dataset_id, run_id = str(uuid4()), str(uuid4())
    mcp_succeeded(
        "run_dataset_pipeline",
        SimpleNamespace(read_only=False),
        {
            "dataset": dataset_id,
            "run": {"mode": mode},
            "job": {"kind": "dataset_pipeline", "id": run_id, "status": "queued"},
        },
        context,
    )
    event = Activity.objects.get(task_id=run_id)
    assert event.action == action
    assert event.actor_name == "Codex"
    assert event.assets[0]["id"] == dataset_id


def test_decision_training_activity_tracks_training_run_and_dataset():
    from types import SimpleNamespace

    from overbae.services.activity import mcp_succeeded

    context = mcp_context(permission=["read", "write"], client_name="codex")
    dataset = Dataset.objects.create(project=context.project, name="Passengers")
    job = FinetuningJob.objects.create(project=context.project, dataset=dataset, status="queued")
    mcp_succeeded(
        "start_decision_job",
        SimpleNamespace(read_only=False),
        {
            "training_run": {"kind": "finetune_job", "id": str(job.pk), "status": "queued"},
            "decision_job": {
                "kind": "decision_job",
                "id": "separate-public-id",
                "status": "queued",
            },
        },
        context,
    )
    event = Activity.objects.get(task_id=str(job.pk))
    assert event.task_kind == "finetune_job"
    assert event.status == "queued"
    assert event.action == "Started training run"
    assert event.assets == [{"kind": "training_job", "id": str(job.pk), "label": job.name}]


def test_preparation_completion_rejects_foreign_outputs_and_unfinished_jobs():
    context = mcp_context(permission=["read", "write"], client_name="codex")
    peer = mcp_context(permission=["read", "write"])
    dataset = Dataset.objects.create(project=context.project, name="Preparing", state="idle")
    foreign = Dataset.objects.create(project=peer.project, name="Foreign", state="idle")
    source = Cell.objects.create(dataset=dataset, position=0, state="ok", fingerprint="a" * 64)
    foreign_cell = Cell.objects.create(
        dataset=foreign, position=0, state="ok", fingerprint="b" * 64
    )
    workflow = call(
        context, "start_dataset_preparation", dataset=str(dataset.pk), request_key="prepare"
    )["preparation_workflow"]
    assert workflow["actor_kind"] == "codex"
    assert workflow["actor_name"] == "Codex"
    args = {
        "dataset": str(dataset.pk),
        "preparation_id": workflow["id"],
        "state": "completed",
        "outputs": [str(foreign_cell.pk)],
    }
    assert asyncio.run(CATALOG.call("update_dataset_preparation", args, context)).isError
    args["outputs"] = [str(source.pk)]
    DatasetPipelineRun.objects.create(
        dataset=dataset,
        source=source,
        source_fingerprint=source.fingerprint,
        request_key="pending",
        state="queued",
    )
    assert asyncio.run(CATALOG.call("update_dataset_preparation", args, context)).isError
    assert asyncio.run(CATALOG.call("update_dataset_preparation", args, peer)).isError
    assert (
        call(context, "inspect_dataset_workbench", dataset=str(dataset.pk))["preparation_workflow"][
            "state"
        ]
        == "preparing"
    )
    call(
        context,
        "update_dataset_preparation",
        dataset=str(dataset.pk),
        preparation_id=workflow["id"],
        state="cancelled",
    )
    assert asyncio.run(CATALOG.call("update_dataset_preparation", args, context)).isError
