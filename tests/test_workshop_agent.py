import hashlib
import io
import json
import uuid
import zipfile

import pytest
from fakes.daytona import DaytonaAPI
from mcp_fixtures import call_tool, mcp_context
from rest_framework.test import APIClient

from overbae.models import Cell, Dataset, WorkshopWorkspace
from overbae.services.datasets import land, partition_assignment, paths, store
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.partition import inherit_provenance
from overbae.services.datasets.rows import RowStoreError
from overbae.services.workshop_agent import briefing, lifecycle, resident, transfer, workspace
from overbae.services.workshop_agent.contracts import StartTask
from workshop_runtime.environment import identity
from workshop_runtime.replay import canonical

pytestmark = [pytest.mark.django_db, pytest.mark.covers("work-once")]


def setup_task():
    context = mcp_context(["read", "write"])
    WorkshopWorkspace.objects.create(project=context.project, sandbox_id=str(uuid.uuid4()))
    dataset = Dataset.objects.create(project=context.project, name="Source", state="ready")
    payload = StartTask(
        dataset=dataset.pk, objective="Inspect and prepare this dataset", request_key="prepare"
    )
    return context, payload


def test_request_retry_preserves_task_and_changed_objective_conflicts():
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    assert lifecycle.start(context, payload).pk == task.pk
    with pytest.raises(DatasetError, match="conflicts"):
        lifecycle.start(context, payload.model_copy(update={"objective": "Different objective"}))


@pytest.mark.parametrize("ready", [True, False])
def test_existing_workspace_runtime_failure_is_visible_and_releases_slot(
    monkeypatch, scripted, ready
):
    context, payload = setup_task()
    dataset = Dataset.objects.get(pk=payload.dataset)
    land.land_rows(dataset, [{"value": 1}])
    task = lifecycle.start(context, payload)
    binding = WorkshopWorkspace.objects.get(project=context.project)
    remote = DaytonaAPI(binding.sandbox_id, {"ready": ready, "identity": "old-release"})
    remote.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")

    assert resident.bootstrap(task) is None

    task.refresh_from_db()
    task.preparation.refresh_from_db()
    binding.refresh_from_db()
    assert task.state == "failed"
    assert task.preparation.state == "cancelled"
    assert task.result["reason"]
    assert binding.task_owner is None
    assert task.actions.get(sequence=0).state == "failed"
    failure = task.chat_events.get(key="workspace-setup-failed").message
    assert failure["status"] == "failed" and failure["text"] == task.result["reason"]
    assert not remote.uploads and not remote.agent_requests


def test_source_briefing_reuses_only_verified_source_and_runtime_facts():
    context, payload = setup_task()
    dataset = Dataset.objects.get(pk=payload.dataset)
    land.land_rows(dataset, [{"label": "yes", "amount": None}, {"label": "no", "amount": 3}])
    dataset.refresh_from_db()
    cell = dataset.active_cell
    first, cold = briefing.prepare(cell, project_id=context.project.pk, runtime="runtime-a")
    again, warm = briefing.prepare(cell, project_id=context.project.pk, runtime="runtime-a")
    assert not cold["cache_hit"] and warm["cache_hit"]
    assert again == first
    assert first["facts"]["columns"]["amount"]["null"] == 1
    assert first["facts"]["rows_measured"] == 2
    _, changed = briefing.prepare(cell, project_id=context.project.pk, runtime="runtime-b")
    assert not changed["cache_hit"]
    for path in (paths.dataset_dir(dataset.pk) / "briefings").glob("*.json"):
        path.write_text('{"facts": {"rows_measured": 999}}')
    repaired, metrics = briefing.prepare(cell, project_id=context.project.pk, runtime="runtime-a")
    assert not metrics["cache_hit"] and repaired == first
    with pytest.raises(ValueError, match="another project"):
        briefing.prepare(cell, project_id=uuid.uuid4(), runtime="runtime-a")
    paths.cell_path(dataset.pk, cell.pk).write_bytes(b"changed source")
    with pytest.raises(RowStoreError, match="frame has changed"):
        briefing.prepare(cell, project_id=context.project.pk, runtime="runtime-a")


def test_context_versions_are_pinned_and_staged_as_separate_local_inputs(monkeypatch, scripted):
    context, payload = setup_task()
    working = Dataset.objects.get(pk=payload.dataset)
    land.land_rows(working, [{"value": 1}])
    reference = Dataset.objects.create(project=context.project, name="Lookup")
    land.land_rows(reference, [{"code": "A", "meaning": "Approved"}])
    reference.refresh_from_db()
    pinned = reference.active_cell
    expected_bytes = paths.cell_path(reference.pk, pinned.pk).read_bytes()
    payload = payload.model_copy(update={"context_datasets": [reference.pk]})
    client = APIClient()
    client.force_authenticate(context.user, context.token)
    body = {
        "project": str(context.project.pk),
        **payload.model_dump(mode="json", exclude={"max_steps"}),
    }
    response = client.post("/api/workshop-tasks/", body, format="json")
    assert response.status_code == 201, response.data
    task = lifecycle.get(response.data["id"])
    pinned.refresh_from_db()
    assert pinned.used_at is not None
    assert response.data["context_sources"][0]["cell"] == str(pinned.pk)

    newer = Cell.objects.create(
        dataset=reference,
        position=pinned.position + 1,
        state="ok",
        fingerprint="different",
    )
    reference.active = newer
    reference.save(update_fields=["active"])
    assert lifecycle.start(context, payload).pk == task.pk
    with pytest.raises(DatasetError, match="conflicts"):
        lifecycle.start(context, payload.model_copy(update={"context_datasets": []}))

    remote = DaytonaAPI(
        WorkshopWorkspace.objects.get(project=context.project).sandbox_id,
        {"ready": True, "identity": identity()},
    )
    remote.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    action, _ = lifecycle.claim(task.pk, 0, "prepare_workshop_environment", {})
    prepared = workspace.prepare(task, action)
    attached = prepared["input"]["context_datasets"][0]
    assert attached["cell"] == str(pinned.pk)
    assert attached["name"] == "Lookup"
    assert attached["sha256"] == hashlib.sha256(expected_bytes).hexdigest()
    assert (
        remote.files[
            workspace.task_root(task) + f"/inputs/context-datasets/{reference.pk}/input.parquet"
        ]
        == expected_bytes
    )
    assert prepared["input"]["cell"] != attached["cell"]
    assert prepared["source_briefing"]["input.json"] == prepared["input"]
    assert (
        prepared["source_briefing"]["context.json"]["source"]["cell"] == prepared["input"]["cell"]
    )


def test_batched_references_reject_any_changed_file(monkeypatch, scripted):
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    remote = DaytonaAPI(
        WorkshopWorkspace.objects.get(project=context.project).sandbox_id,
        {"ready": True, "identity": identity()},
    )
    remote.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    sandbox = workspace.attach(task)
    files = {
        "/context/first.json": b'{"task":"inspect"}',
        "/context/second.md": b"Read the source.",
    }
    workspace.upload_references(sandbox, files)
    assert all(remote.files[name] == content for name, content in files.items())
    remote.corrupt_file = "/context/second.md"
    with pytest.raises(ValueError, match="checksum mismatch"):
        workspace.upload_references(sandbox, files)


def test_compressed_replay_transfer_preserves_files_and_rejects_corruption(monkeypatch, scripted):
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    remote = DaytonaAPI(WorkshopWorkspace.objects.get(project=context.project).sandbox_id, {})
    remote.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    sandbox = workspace.attach(task)
    files = {
        "/replay/output.jsonl": b'{"text":"repeated evidence"}\n' * 1000,
        "/replay/package.zip": bytes(range(256)),
    }
    remote.files.update(files)
    with workspace.download_replay_files(sandbox, list(files)) as downloaded:
        assert {name: path.read_bytes() for name, path in downloaded.items()} == files
    assert all(not path.exists() for path in downloaded.values())
    remote.corrupt_file = "/tmp/workshop-transfer-fixture.zip"
    with (
        pytest.raises(ValueError, match="transfer checksum mismatch"),
        workspace.download_replay_files(sandbox, list(files)),
    ):
        pytest.fail("Corrupt transfer was accepted")


def test_replay_digest_accepts_streams_without_changing_native_fingerprints():
    records = [{"text": "café\u2028continued", "nested": [None, True, 1.5], "count": 2}]
    assert resident.canonical(iter(records)) == canonical(records)
    assert resident.canonical(iter([])) == canonical([])


@pytest.mark.parametrize("corruption", ["checksum", "oversize", "truncated"])
def test_source_upload_verifies_exact_bytes_reuses_receipts_and_preserves_previous_on_failure(
    monkeypatch, scripted, tmp_path, corruption
):
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    remote = DaytonaAPI(WorkshopWorkspace.objects.get(project=context.project).sandbox_id, {})
    remote.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    sandbox = workspace.attach(task)
    source = tmp_path / "source.jsonl"
    content = '{"text":"café\u2028continued"}\n'.encode() * 1000
    source.write_bytes(content)
    destination = workspace.task_root(task) + "/inputs/quoted ' source.jsonl"
    first = transfer.copy_verified(sandbox, source, destination)
    assert remote.files[destination] == content
    assert first["sha256"] == hashlib.sha256(content).hexdigest()
    assert first["transfer"]["uploaded_bytes"] < len(content)
    uploads = len(remote.uploads)
    repeated = transfer.copy_verified(sandbox, source, destination)
    assert repeated["transfer"]["reused"] and len(remote.uploads) == uploads
    source.write_bytes(b"changed source")
    remote.corrupt_upload = corruption
    with pytest.raises(ValueError, match="verification failed"):
        transfer.copy_verified(sandbox, source, destination)
    assert remote.files[destination] == content
    remote.corrupt_upload = False
    repaired = transfer.copy_verified(sandbox, source, destination)
    assert not repaired["transfer"]["reused"]
    assert remote.files[destination] == b"changed source"
    source.write_bytes(b"")
    transfer.copy_verified(sandbox, source, destination)
    assert remote.files[destination] == b""
    assert not any(".upload-" in name or ".overmind-install-" in name for name in remote.files)


@pytest.mark.covers("tenancy")
@pytest.mark.parametrize("change", ["sandbox", "generation", "owner", "disabled"])
def test_retained_workspace_transport_rechecks_lease_before_every_exchange(
    monkeypatch, scripted, change
):
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    binding = WorkshopWorkspace.objects.get(project=context.project)
    binding.task_owner = task.pk
    binding.save(update_fields=["task_owner"])
    action, _ = lifecycle.claim(task.pk, 0, "prepare_workshop_environment", {})
    action.receipt = {"sandbox": binding.sandbox_id, "generation": binding.generation}
    action.save(update_fields=["receipt"])
    remote = DaytonaAPI(binding.sandbox_id, {"ready": True, "identity": identity()})
    remote.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    sandbox = workspace.attach(task)
    resident.call(task, "exchange", {"control": "running"}, sandbox=sandbox)
    if change == "sandbox":
        binding.sandbox_id = str(uuid.uuid4())
    elif change == "generation":
        binding.generation += 1
    elif change == "owner":
        binding.task_owner = None
    else:
        binding.enabled = False
    binding.save()
    with pytest.raises((ValueError, WorkshopWorkspace.DoesNotExist)):
        resident.call(task, "exchange", {"control": "running"}, sandbox=sandbox)
    assert remote.agent_requests == [{"control": "running"}]


@pytest.mark.covers("tenancy")
def test_context_selection_rejects_foreign_missing_unreadable_and_repeated_datasets():
    context, payload = setup_task()
    other = mcp_context(["read", "write"])
    foreign = Dataset.objects.create(project=other.project)
    unreadable = Dataset.objects.create(project=context.project)
    for selection in (
        [foreign.pk],
        [uuid.uuid4()],
        [unreadable.pk],
        [payload.dataset],
        [unreadable.pk, unreadable.pk],
    ):
        result = call_tool(
            "start_workshop_task",
            payload.model_copy(update={"context_datasets": selection}).model_dump(mode="json"),
            context,
        )
        assert result.isError
    assert not context.project.workshoptask_set.exists()


def test_uncertain_action_is_not_claimed_again_and_blocks_resume():
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    action, created = lifecycle.claim(
        task.pk, 0, "execute_python", {"command": "python transform.py"}
    )
    assert created
    lifecycle.record_action(action.pk, "unknown", {})
    recovered, created = lifecycle.claim(task.pk, 0, action.name, action.arguments)
    assert not created and recovered.pk == action.pk and recovered.state == "unknown"
    with pytest.raises(DatasetError, match="uncertain"):
        lifecycle.control(context.project, task.pk, "resume")


def test_interrupted_publication_can_resume_but_unknown_model_call_cannot():
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    publication, _ = lifecycle.claim(task.pk, 0, "publish_workshop", {"replay": "retained"})
    lifecycle.control(context.project, task.pk, "pause")
    assert lifecycle.control(context.project, task.pk, "resume").desired_state == "running"
    publication.refresh_from_db()
    assert publication.state == "intent"
    model, _ = lifecycle.claim(task.pk, 1, "reason", {})
    lifecycle.record_action(model.pk, "unknown", {})
    with pytest.raises(DatasetError, match="uncertain"):
        lifecycle.control(context.project, task.pk, "resume")


@pytest.mark.covers("tenancy")
def test_task_controls_cannot_cross_projects():
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    other = mcp_context(["read", "write"])
    response = call_tool(
        "control_workshop_task", {"task": str(task.pk), "command": "cancel"}, other
    )
    assert response.isError
    task.refresh_from_db()
    assert task.desired_state == "running"


@pytest.mark.covers("tenancy")
def test_console_task_start_retry_and_cross_project_control():
    context, payload = setup_task()
    client = APIClient()
    client.force_authenticate(context.user, context.token)
    body = {
        "project": str(context.project.pk),
        **payload.model_dump(mode="json", exclude={"max_steps"}),
    }
    response = client.post("/api/workshop-tasks/", body, format="json")
    assert response.status_code == 201, response.data
    task_id = response.data["id"]
    assert client.post("/api/workshop-tasks/", body, format="json").data["id"] == task_id
    listed = client.get(
        "/api/workshop-tasks/",
        {"project": str(context.project.pk), "dataset": str(payload.dataset)},
    )
    assert listed.data["enabled"] and listed.data["tasks"][0]["id"] == task_id
    other = mcp_context(["read", "write"])
    client.force_authenticate(other.user, other.token)
    assert (
        client.post(
            f"/api/workshop-tasks/{task_id}/control/", {"command": "cancel"}, format="json"
        ).status_code
        == 404
    )


def test_resident_publication_reuses_shared_lineage_and_checks_retained_bytes(
    monkeypatch, scripted, tmp_path
):
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    land.land_rows(task.dataset, [{"value": i} for i in range(12)])
    task = lifecycle.get(task.pk)
    source = task.dataset.active_cell
    action, _ = lifecycle.claim(task.pk, 0, "prepare_workshop_environment", {})
    original = list(store.iter_rows(paths.cell_path(task.dataset_id, source.pk)))
    incoming = [
        {k: v for k, v in row.items() if not k.startswith("_overmind_")} for row in original
    ]
    result = [{"source_row": row["source_row"], "value": row["value"] * 2} for row in incoming]
    action.result = {
        "runtime": {"identity": identity()},
        "input": {
            "cell": str(source.pk),
            "fingerprint": source.fingerprint,
            "sha256": source.fingerprint,
        },
    }
    slot = WorkshopWorkspace.objects.get(project=context.project)
    slot.task_owner = task.pk
    slot.save(update_fields=["task_owner"])
    action.receipt = {"sandbox": slot.sandbox_id, "generation": slot.generation}
    action.state = "completed"
    action.save()
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "version": 1,
                    "runtime": identity(),
                    "parameters": {},
                    "steps": [
                        {
                            "id": "scale",
                            "name": "Scale",
                            "entrypoint": "scale.py",
                            "input_schema": {"value": "integer"},
                            "output_schema": {"source_row": "integer", "value": "integer"},
                            "checks": {"preserve_rows": True},
                        }
                    ],
                }
            ),
        )
        archive.writestr(
            "scale.py",
            "raise AssertionError('Publication must consume the local replay, not execute code again')\n",
        )
        archive.writestr("notes.md", "# Evidence\nEvery source value is doubled.\n")
    replay_id = str(uuid.uuid4())
    receipt = {
        "id": replay_id,
        "state": "completed",
        "runtime": identity(),
        "package_sha256": hashlib.sha256(bundle.getvalue()).hexdigest(),
        "source_sha256": source.fingerprint,
        "parameters": {},
        "output_rows": len(result),
        "steps": [
            {
                "input_sha256": canonical(incoming),
                "output_sha256": canonical(result),
                "exit_code": 0,
                "rows": len(result),
            }
        ],
    }
    partition = partition_assignment.validate_recipe(
        {
            "seed": 42,
            "fractions": {"train": 0.75, "final": 0.25},
            "group_by": ["value"],
            "stratify_by": None,
            "holdouts": [],
        }
    )
    local_rows = [
        inherit_provenance(row, [original[index]], [{"row": index}])
        for index, row in enumerate(result)
    ]
    receipt["partition"] = partition_assignment.assign(
        local_rows, len(local_rows), partition, tmp_path
    )
    assignments = (tmp_path / "assignments.jsonl").read_bytes()
    receipt["partition_recipe"] = partition
    receipt["assignments_sha256"] = hashlib.sha256(assignments).hexdigest()
    remote = DaytonaAPI(
        WorkshopWorkspace.objects.get(project=context.project).sandbox_id, {"ready": True}
    )
    root = workspace.task_root(task) + f"/.agent/replays/{replay_id}"
    remote.files.update(
        {
            root + "/receipt.json": json.dumps(receipt).encode(),
            root + "/package.zip": bundle.getvalue(),
            root + "/output-0.jsonl": "\n".join(json.dumps(row) for row in result).encode(),
        }
    )
    remote.files[root + "/assignments.jsonl"] = assignments
    remote.connect(monkeypatch, scripted)
    monkeypatch.setenv("DAYTONA_API_KEY", "test-daytona-key")
    request = {"id": str(uuid.uuid4()), "arguments": {"replay": replay_id, "name": "Scaled"}}
    published = resident.publish(task, request)
    assert resident.publish(task, request) == published
    assert published["verified_against_replay"]
    cell = Cell.objects.get(pk=published["cell"])
    assert cell.rows == 12 and cell.review["execution"] == "native_workspace"
    assert len(published["outputs"]) == 3
    assert lifecycle.completion_outputs(task, published["outputs"]) == [str(cell.pk)]
    rows = list(store.iter_rows(paths.cell_path(task.dataset_id, cell.pk)))
    assert [row["value"] for row in rows] == list(range(0, 24, 2))
    assert all(row["_overmind_provenance"] for row in rows)
    receipt["partition"]["counts"]["train"] += 1
    remote.files[root + "/receipt.json"] = json.dumps(receipt).encode()
    with pytest.raises(ValueError, match="partition does not match"):
        resident.publish(task, request)
    receipt["partition"]["counts"]["train"] -= 1
    receipt["output_rows"] += 1
    remote.files[root + "/receipt.json"] = json.dumps(receipt).encode()
    with pytest.raises(ValueError, match="final count does not match"):
        resident.publish(task, request)
    receipt["output_rows"] -= 1
    remote.files[root + "/receipt.json"] = json.dumps(receipt).encode()
    remote.files[root + "/output-0.jsonl"] = b'{"source_row":0,"value":999}\n'
    with pytest.raises(ValueError, match="changed after"):
        resident.publish(task, request)


def test_resident_completion_locks_only_task_and_finishes_preparation():
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    land.land_rows(task.dataset, [{"value": 1}])
    task = lifecycle.get(task.pk)
    slot = WorkshopWorkspace.objects.get(project=context.project)
    slot.task_owner = task.pk
    slot.save(update_fields=["task_owner"])
    resident.project_state(
        task,
        {
            "state": {
                "state": "completed",
                "turn": 2,
                "publications": [{"outputs": [str(task.dataset.active_cell.pk)]}],
            }
        },
    )
    task.refresh_from_db()
    task.preparation.refresh_from_db()
    slot.refresh_from_db()
    assert task.state == "completed"
    assert task.preparation.state == "completed"
    assert slot.task_owner is None


def test_cancelled_preparation_revokes_resident_publication_authority():
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    task.preparation.state = "cancelled"
    task.preparation.save(update_fields=["state"])
    with pytest.raises(DatasetError, match="preparation has ended"):
        lifecycle.authorise(task)


@pytest.mark.parametrize(
    ("alive", "pending", "uncertain", "last_tool", "release"),
    [
        (False, [], False, "finish", True),
        (True, [], False, "finish", False),
        (False, [{"kind": "model"}], False, "finish", False),
        (False, [], True, "finish", False),
        (False, [], False, "python", False),
    ],
)
def test_settled_clarification_releases_workspace_without_discarding_task(
    alive, pending, uncertain, last_tool, release
):
    context, payload = setup_task()
    task = lifecycle.start(context, payload)
    slot = WorkshopWorkspace.objects.get(project=context.project)
    slot.task_owner = task.pk
    slot.save(update_fields=["task_owner"])
    if uncertain:
        action, _ = lifecycle.claim(task.pk, 0, "reason", {})
        lifecycle.record_action(action.pk, "unknown", {})
    snapshot = {
        "alive": alive,
        "pending": pending,
        "state": {
            "state": "blocked",
            "turn": 1,
            "reason": "The target meaning requires clarification.",
            "events": [{"turn": 0, "tool": last_tool, "error": None}],
        },
    }
    resident.project_state(task, snapshot)
    slot.refresh_from_db()
    task.refresh_from_db()
    task.preparation.refresh_from_db()
    assert (slot.task_owner is None) is release
    assert task.state == "blocked" and task.preparation.state == "paused"
    assert task.result["reason"] == snapshot["state"]["reason"]
    if release:
        next_dataset = Dataset.objects.create(project=context.project, name="Another source")
        next_task = lifecycle.start(
            context,
            payload.model_copy(update={"dataset": next_dataset.pk, "request_key": "next"}),
        )
        slot.task_owner = next_task.pk
        slot.save(update_fields=["task_owner"])
        resident.project_state(task, snapshot)
        slot.refresh_from_db()
        assert slot.task_owner == next_task.pk
