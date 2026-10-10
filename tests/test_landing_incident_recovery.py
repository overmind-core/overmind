import base64
import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from django.core.management.base import CommandError
from django.db import close_old_connections, connection
from django.utils import timezone

from overbae.management.commands.recover_landing_incident import (
    ORIGINAL_INPUTS,
    TARGET_DATASET,
    TARGET_PROJECT,
    TARGET_TASK,
    TARGET_UPLOAD,
    confirmed_noop,
    decode_target_envelope,
    retry_preserved_import,
    validate_dataset,
    validate_preserved_import,
)
from overbae.models import Dataset, DatasetImport, Project
from overbae.services.datasets import files, imports
from overbae.services.datasets.lifecycle import DatasetError
from overbae.tasks import datasets as dataset_tasks


def envelope(**changes):
    kwargs = {
        "dataset_id": TARGET_DATASET,
        "source": {"uploads": [TARGET_UPLOAD]},
        "user_id": "3",
        "infer_capability": False,
    }
    kwargs.update(changes)
    return json.dumps(
        {
            "headers": {"id": TARGET_TASK, "task": "overbae.tasks.datasets.land"},
            "body": base64.b64encode(json.dumps([[], kwargs, {}]).encode()).decode(),
            "properties": {"body_encoding": "base64"},
        }
    ).encode()


def test_recovery_decodes_only_original_task_source_identity():
    assert decode_target_envelope(envelope())["source"] == {"uploads": [TARGET_UPLOAD]}
    with pytest.raises(CommandError):
        decode_target_envelope(envelope(dataset_id="00000000-0000-0000-0000-000000000001"))
    with pytest.raises(CommandError):
        decode_target_envelope(envelope(source={"uploads": [TARGET_UPLOAD, "another-upload"]}))
    with pytest.raises(CommandError):
        decode_target_envelope(envelope(split={"eval_dataset_id": "another-dataset"}))


@pytest.mark.django_db
def test_error_repair_is_limited_to_empty_exact_watchdog_failure():
    project = Project.objects.create(id=TARGET_PROJECT, name="Incident", slug="incident")
    dataset = Dataset.objects.create(
        id=TARGET_DATASET,
        project=project,
        name="Titanic",
        state="error",
        error="The worker stopped before this finished.",
    )
    validate_dataset(dataset)
    dataset.error = "User source parsing failed"
    with pytest.raises(CommandError):
        validate_dataset(dataset)
    dataset.error = "The worker stopped before this finished."
    dataset.operation = {"state": "running", "task_id": "another-operation"}
    with pytest.raises(CommandError):
        validate_dataset(dataset)
    dataset.chat = []
    dataset.state = "diagnosing"
    with pytest.raises(CommandError):
        validate_dataset(dataset)


def test_noop_retry_requires_a_known_exact_terminal_result_and_original_choices():
    assert confirmed_noop({"status": "SUCCESS", "result": {"status": "landed"}})
    for meta in (
        {"status": "PENDING", "result": None},
        {"status": "STARTED", "result": None},
        {"status": "FAILURE", "result": {"status": "landed"}},
        {"status": "SUCCESS", "result": {"status": "landed", "rows": 891}},
        {"status": "SUCCESS", "result": {"status": "unknown"}},
    ):
        assert not confirmed_noop(meta)
    with pytest.raises(CommandError):
        decode_target_envelope(envelope(infer_capability=True))
    with pytest.raises(CommandError):
        decode_target_envelope(envelope(user_id="another-user"))


@pytest.fixture
def preserved_import():
    error = "The source import stopped before publication. Retry the import."
    project = Project.objects.create(id=TARGET_PROJECT, name="Incident", slug="incident")
    dataset = Dataset.objects.create(
        id=TARGET_DATASET, project=project, name="Titanic", state="error", error=error
    )
    run = DatasetImport.objects.create(
        id=TARGET_TASK,
        dataset=dataset,
        state="blocked",
        failure_code="legacy_interrupted",
        error=error,
        inputs=ORIGINAL_INPUTS,
        source_manifest=[
            {
                "upload_id": TARGET_UPLOAD,
                "filename": "titanic.csv",
                "bytes": 60_302,
                "mtime_ns": 12345,
            }
        ],
        queued_at=timezone.now(),
        published_at=timezone.now(),
    )
    return dataset, run


@pytest.mark.django_db
def test_preserved_retry_accepts_only_the_untouched_legacy_receipt(preserved_import):
    dataset, run = preserved_import
    validate_preserved_import(run, dataset)
    for changes in (
        {"id": uuid.uuid4()},
        {"dataset_id": uuid.uuid4()},
        {"state": "queued"},
        {"failure_code": "worker_timeout"},
        {"error": "A different import error"},
        {"inputs": {**ORIGINAL_INPUTS, "infer_capability": True}},
        {"inputs": {**ORIGINAL_INPUTS, "user_id": "another-user"}},
        {"source_manifest": []},
        {"source_manifest": [{**run.source_manifest[0], "upload_id": str(uuid.uuid4())}]},
        {"source_manifest": [{**run.source_manifest[0], "bytes": 100}]},
        {"owner": uuid.uuid4()},
        {"lease_until": timezone.now()},
        {"publish_owner": uuid.uuid4()},
        {"attempts": 1},
        {"started_at": timezone.now()},
        {"evaluation_id": uuid.uuid4()},
        {"result": {"rows": 891}},
    ):
        candidate = DatasetImport.objects.get(pk=TARGET_TASK)
        for field, value in changes.items():
            setattr(candidate, field, value)
        with pytest.raises(CommandError):
            validate_preserved_import(candidate, dataset)
    dataset.operation = {"state": "running", "task_id": "another-operation"}
    with pytest.raises(CommandError):
        validate_preserved_import(run, dataset)
    dataset.chat = []
    dataset.error = "An unrelated source error"
    with pytest.raises(CommandError):
        validate_preserved_import(run, dataset)


@pytest.mark.django_db
@pytest.mark.parametrize("apply", [False, True])
def test_preserved_retry_refuses_changed_bytes_before_any_state_change(
    preserved_import, tmp_path, settings, apply
):
    settings.MEDIA_ROOT = tmp_path
    dataset, run = preserved_import
    path = files.upload_data_path(TARGET_UPLOAD)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"x" * 60_302)
    (path.parent / "name").write_text("titanic.csv")
    with pytest.raises(CommandError, match="SHA-256"):
        retry_preserved_import(apply=apply)
    run.refresh_from_db()
    dataset.refresh_from_db()
    assert run.state == "blocked" and run.failure_code == "legacy_interrupted"
    assert run.owner is None and run.publish_attempts == 0
    assert dataset.state == "error" and dataset.source is None
    assert path.exists()


@pytest.mark.django_db(transaction=True)
def test_concurrent_preserved_import_resumes_publish_one_durable_retry(
    tmp_path, settings, monkeypatch
):
    assert connection.vendor == "postgresql", "Recovery requires real row-lock semantics"
    settings.MEDIA_ROOT = tmp_path
    project = Project.objects.create(name="Concurrent recovery", slug="concurrent-recovery")
    upload_id, _filename = files.begin_upload("passengers.csv")
    files.append_chunk(upload_id, 0, b"passenger_id,survived\n1,0\n2,1\n")
    files.inspect_upload(upload_id, size=files.upload_received(upload_id))
    dataset = Dataset.objects.create(
        project=project, name="Passengers", state="error", error="The worker stopped."
    )
    inputs = {
        "dataset_id": str(dataset.pk),
        "source": {"uploads": [upload_id]},
        "user_id": None,
        "infer_capability": False,
    }
    task_id = uuid.uuid4()
    assert imports.execute(str(task_id), inputs) == {"status": "blocked"}
    deliveries = []
    monkeypatch.setattr(
        dataset_tasks.land, "apply_async", lambda **kwargs: deliveries.append(kwargs)
    )
    barrier = Barrier(2)

    def resume():
        close_old_connections()
        try:
            barrier.wait(timeout=5)
            try:
                imports.resume(task_id)
                return "resumed"
            except DatasetError as exc:
                return exc.code
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            future.result(timeout=15) for future in [pool.submit(resume), pool.submit(resume)]
        ]
    assert sorted(results) == ["import_busy", "resumed"]
    assert len(deliveries) == 1
    assert deliveries[0]["task_id"] == str(task_id)
    assert deliveries[0]["kwargs"] == inputs
    run = DatasetImport.objects.get(pk=task_id)
    dataset.refresh_from_db()
    assert run.state == "queued" and run.attempts == 0
    assert dataset.state == "landing" and not dataset.error
    with pytest.raises(DatasetError) as repeated:
        imports.resume(task_id)
    assert repeated.value.code == "import_busy" and len(deliveries) == 1
