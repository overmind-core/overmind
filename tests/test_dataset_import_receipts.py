"""Durable imports must survive queue delay, delivery duplication and worker loss.

Concrete regressions: queue age was mistaken for worker execution age, broker
publication could strand an unbound source, a late message reported 'landed'
without importing, and cleanup could delete the only source after a failure.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.models import Dataset, DatasetImport, Project
from overbae.services.datasets import dispatch, files
from overbae.services.datasets.lifecycle import DatasetError
from overbae.tasks import datasets as dataset_tasks
from overbae.tasks.cleanup_tmp import cleanup_uploads

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def imported_source(tmp_path, settings, monkeypatch):
    settings.MEDIA_ROOT = tmp_path
    project = Project.objects.create(name="Queued imports", slug=f"imports-{uuid.uuid4().hex[:10]}")
    upload_id, filename = files.begin_upload("passengers.csv")
    files.append_chunk(upload_id, 0, b"passenger_id,survived\n1,0\n2,1\n")
    files.inspect_upload(upload_id, size=files.upload_received(upload_id))
    deliveries = []
    monkeypatch.setattr(
        dataset_tasks.land, "apply_async", lambda **kwargs: deliveries.append(kwargs)
    )
    return project, upload_id, filename, deliveries


def create_import(imported_source):
    project, upload_id, _filename, _deliveries = imported_source
    dataset = dispatch.create_dataset(
        project=project,
        user=None,
        name="Passengers",
        source={"uploads": [upload_id]},
        intent="train",
    )
    run = DatasetImport.objects.get(dataset=dataset)
    return dataset, run


def test_source_and_task_receipt_exist_before_broker_publication(imported_source, monkeypatch):
    project, upload_id, _filename, deliveries = imported_source

    def publish(**delivery):
        dataset = Dataset.objects.get(pk=delivery["kwargs"]["dataset_id"])
        run = DatasetImport.objects.get(pk=delivery["task_id"], dataset=dataset)
        assert run.state == "queued"
        assert run.inputs["source"] == {"uploads": [upload_id]}
        assert run.dataset_id == dataset.pk and run.queued_at is not None
        deliveries.append(delivery)

    monkeypatch.setattr(dataset_tasks.land, "apply_async", publish)
    dataset = dispatch.create_dataset(
        project=project, user=None, name="Passengers", source={"uploads": [upload_id]}
    )
    assert dataset.state == "landing" and len(deliveries) == 1


def test_broker_failure_keeps_recoverable_source_and_same_task_identity(
    imported_source, monkeypatch
):
    from overbae.services.datasets import imports

    def unavailable(**kwargs):
        raise ConnectionError("offline test broker")

    monkeypatch.setattr(dataset_tasks.land, "apply_async", unavailable)
    dataset, run = create_import(imported_source)
    assert dataset.state == "landing"
    assert run.state == "queued" and run.failure_code == "dispatch_failed"
    assert files.upload_data_path(imported_source[1]).exists()

    deliveries = []
    monkeypatch.setattr(
        dataset_tasks.land, "apply_async", lambda **kwargs: deliveries.append(kwargs)
    )
    DatasetImport.objects.filter(pk=run.pk).update(
        next_publish_at=timezone.now() - timedelta(seconds=1)
    )
    imports.publish(run.pk)
    run.refresh_from_db()
    assert deliveries[0]["task_id"] == str(run.pk)
    assert deliveries[0]["kwargs"] == run.inputs


def test_queue_age_does_not_count_as_execution_age(imported_source, settings):
    settings.DATASET_IMPORT_MAX_QUEUE_SECONDS = 90 * 60
    dataset, run = create_import(imported_source)
    old = timezone.now() - timedelta(minutes=66)
    Dataset.objects.filter(pk=dataset.pk).update(updated_at=old)
    DatasetImport.objects.filter(pk=run.pk).update(
        created_at=old,
        updated_at=old,
        queued_at=old,
    )
    dataset_tasks.reap_stuck_runs()
    dataset.refresh_from_db()
    run.refresh_from_db()
    assert dataset.state == "landing" and dataset.error == ""
    assert run.state == "queued"


def test_duplicate_delivery_cannot_claim_an_owned_import(imported_source):
    from overbae.services.datasets import imports

    dataset, run = create_import(imported_source)
    claim = imports.claim(run.pk)
    assert claim is not None
    assert imports.claim(run.pk) is None
    run.refresh_from_db()
    assert run.state == "running" and run.owner == claim.owner
    assert run.started_at is not None
    assert run.inputs["source"] == {"uploads": [imported_source[1]]}


def test_claim_starts_a_new_execution_clock_after_long_queue_wait(imported_source):
    from overbae.services.datasets import imports

    dataset, run = create_import(imported_source)
    old = timezone.now() - timedelta(minutes=66)
    Dataset.objects.filter(pk=dataset.pk).update(updated_at=old)
    assert imports.claim(run.pk) is not None
    dataset_tasks.reap_stuck_runs()
    dataset.refresh_from_db()
    assert dataset.state == "landing"
    assert dataset.updated_at > old


def test_an_import_whose_worker_died_is_requeued_and_its_old_owner_fenced(imported_source):
    from overbae.services.datasets import imports

    dataset, run = create_import(imported_source)
    deliveries = imported_source[3]
    dead = imports.claim(run.pk)
    assert dead is not None
    lapsed = timezone.now() - timedelta(seconds=1)
    DatasetImport.objects.filter(pk=run.pk).update(lease_until=lapsed)
    published = len(deliveries)
    imports.reconcile()
    run.refresh_from_db()
    dataset.refresh_from_db()
    assert (run.state, dataset.state) == ("queued", "landing")
    assert len(deliveries) == published + 1
    with pytest.raises(DatasetError) as caught, imports.publication(dead):
        pass
    assert caught.value.code == "ownership_lost"
    assert imports.claim(run.pk) is not None


def test_an_import_that_keeps_losing_its_worker_is_blocked_with_its_source(imported_source):
    from overbae.services.datasets import imports

    dataset, run = create_import(imported_source)
    for _ in range(imports.MAX_ATTEMPTS):
        assert imports.claim(run.pk) is not None
        DatasetImport.objects.filter(pk=run.pk).update(
            lease_until=timezone.now() - timedelta(seconds=1)
        )
        imports.reconcile()
    run.refresh_from_db()
    dataset.refresh_from_db()
    assert run.state == "blocked" and run.failure_code == "worker_timeout"
    assert dataset.state == "error"
    assert files.upload_data_path(imported_source[1]).exists()


def test_a_live_import_renews_its_lease_only_up_to_the_execution_limit(imported_source):
    from overbae.services.datasets import imports

    _dataset, run = create_import(imported_source)
    live = imports.claim(run.pk)
    imports.renew(live)
    run.refresh_from_db()
    assert run.lease_until > timezone.now() + timedelta(seconds=imports.LEASE_SECONDS - 5)
    started = timezone.now() - timedelta(seconds=imports.EXECUTION_SECONDS)
    DatasetImport.objects.filter(pk=run.pk).update(started_at=started)
    imports.renew(live)
    run.refresh_from_db()
    assert run.lease_until <= started + timedelta(
        seconds=imports.EXECUTION_SECONDS + imports.LEASE_GRACE_SECONDS
    )


def test_old_owner_cannot_publish_after_import_is_resumed(imported_source):
    from overbae.services.datasets import imports

    dataset, run = create_import(imported_source)
    old_claim = imports.claim(run.pk)
    imports.fail(old_claim, "The first attempt stopped.")
    imports.resume(run.pk)
    new_claim = imports.claim(run.pk)
    assert new_claim is not None and new_claim.owner != old_claim.owner
    with pytest.raises(DatasetError) as caught, imports.publication(old_claim):
        Dataset.objects.filter(pk=dataset.pk).update(name="Stale owner published")
    assert caught.value.code == "ownership_lost"
    dataset.refresh_from_db()
    assert dataset.name == "Passengers"
    run.refresh_from_db()
    assert run.owner == new_claim.owner


def test_failed_import_source_survives_upload_cleanup(imported_source):
    from overbae.services.datasets import imports

    dataset, run = create_import(imported_source)
    claim = imports.claim(run.pk)
    imports.fail(claim, "The parser stopped before publication.")
    upload = files.upload_dir(imported_source[1])
    old = (timezone.now() - timedelta(hours=25)).timestamp()
    os.utime(upload / "data", (old, old))
    os.utime(upload, (old, old))
    cleanup_uploads()
    assert (upload / "data").exists()
    run.refresh_from_db()
    assert run.state == "blocked"
    assert run.inputs["source"]["uploads"] == [imported_source[1]]


def test_queue_budget_blocks_visibly_and_resume_keeps_exact_source(imported_source, settings):
    from overbae.services.datasets import imports

    settings.DATASET_IMPORT_MAX_QUEUE_SECONDS = 60
    dataset, run = create_import(imported_source)
    old = timezone.now() - timedelta(minutes=2)
    DatasetImport.objects.filter(pk=run.pk).update(
        created_at=old,
        updated_at=old,
        queued_at=old,
    )
    imports.reconcile()
    run.refresh_from_db()
    dataset.refresh_from_db()
    assert run.state == "blocked" and run.failure_code == "queue_timeout"
    assert dataset.state == "error"
    assert imports.claim(run.pk) is None
    assert files.upload_data_path(imported_source[1]).exists()
    imports.resume(run.pk)
    run.refresh_from_db()
    assert run.state == "queued"
    assert run.inputs["source"] == {"uploads": [imported_source[1]]}
    assert imports.claim(run.pk) is not None


def test_pasted_rows_are_spooled_instead_of_stored_in_database_json(imported_source):
    project, _upload_id, _filename, _deliveries = imported_source
    rows = [{"question": "one", "answer": "a"}, {"question": "two", "answer": "b"}]
    dataset = dispatch.create_dataset(
        project=project, user=None, name="Pasted", source={"rows": rows}
    )
    run = DatasetImport.objects.get(dataset=dataset)
    source = run.inputs["source"]
    assert "rows" not in source
    assert len(source["uploads"]) == 1
    path = files.upload_data_path(source["uploads"][0])
    assert [json.loads(line) for line in path.read_text().splitlines()] == rows


def test_split_outputs_share_one_durable_import(imported_source):
    project, upload_id, _filename, deliveries = imported_source
    train, evaluation = dispatch.create_split(
        project=project,
        user=None,
        name="Passengers",
        source={"uploads": [upload_id]},
        eval_percent=50,
        position="head",
    )
    run = DatasetImport.objects.get(dataset=train)
    train.refresh_from_db()
    evaluation.refresh_from_db()
    assert run.evaluation_id == evaluation.pk
    assert run.inputs["split"]["eval_dataset_id"] == str(evaluation.pk)
    assert len(deliveries) == 1


def test_real_csv_lands_once_and_retains_no_source_after_commit(imported_source, monkeypatch):
    from overbae.services.datasets import imports, paths, store

    dataset, run = create_import(imported_source)
    handoffs = []
    monkeypatch.setattr(
        dataset_tasks.diagnose, "apply_async", lambda **kwargs: handoffs.append(kwargs)
    )
    result = imports.execute(str(run.pk), run.inputs)
    assert result == {"status": "ok", "rows": 2}
    dataset.refresh_from_db()
    run.refresh_from_db()
    assert dataset.state == "diagnosing" and run.state == "complete"
    assert dataset.cells.count() == 1
    frame = store.read_frame(paths.cell_path(dataset.pk, dataset.source.pk))
    assert frame["passenger_id"].tolist() == [1, 2]
    assert frame["survived"].tolist() == [0, 1]
    assert not files.upload_data_path(imported_source[1]).exists()
    assert len(handoffs) == 1
    assert imports.execute(str(run.pk), run.inputs) == {"status": "complete"}
    assert dataset.cells.count() == 1 and len(handoffs) == 1


def test_existing_run_action_resumes_blocked_source_import(imported_source, monkeypatch):
    from overbae.services.datasets import imports

    dataset, run = create_import(imported_source)
    claim = imports.claim(run.pk)
    imports.fail(claim, "The worker stopped before publication.")
    dataset.refresh_from_db()
    assert dataset.state == "error" and dataset.source is None
    dataset = dispatch.run_dataset(dataset, None)
    run.refresh_from_db()
    assert dataset.state == "landing" and run.state == "queued"
    monkeypatch.setattr(dataset_tasks.diagnose, "apply_async", lambda **kwargs: None)
    assert imports.execute(str(run.pk), run.inputs) == {"status": "ok", "rows": 2}


def test_adopting_an_existing_message_does_not_publish_again(imported_source):
    from overbae.services.datasets import imports

    project, upload_id, _filename, deliveries = imported_source
    dataset = Dataset.objects.create(
        project=project,
        name="Legacy",
        state="error",
        error="The worker stopped before this finished.",
    )
    inputs = {
        "dataset_id": str(dataset.pk),
        "source": {"uploads": [upload_id]},
        "user_id": None,
        "infer_capability": True,
    }
    run = imports.queue_landing_receipt(dataset, uuid.uuid4(), inputs, published=True)
    imports.reconcile()
    dataset.refresh_from_db()
    assert dataset.state == "landing" and run.published_at is not None
    assert run.inputs == inputs and not deliveries


def test_legacy_error_delivery_preserves_source_without_restarting(imported_source):
    from overbae.services.datasets import imports

    project, upload_id, _filename, deliveries = imported_source
    dataset = Dataset.objects.create(
        project=project,
        name="Legacy",
        state="error",
        error="The worker stopped before this finished.",
    )
    task_id = str(uuid.uuid4())
    inputs = {
        "dataset_id": str(dataset.pk),
        "source": {"uploads": [upload_id]},
        "user_id": None,
        "infer_capability": True,
    }
    assert imports.execute(task_id, inputs) == {"status": "blocked"}
    dataset.refresh_from_db()
    run = DatasetImport.objects.get(pk=task_id)
    assert dataset.state == "error" and dataset.source is None
    assert run.failure_code == "legacy_interrupted"
    assert files.upload_data_path(upload_id).exists() and not deliveries


def test_concurrent_handoff_publisher_cannot_publish_the_same_turn(imported_source, monkeypatch):
    from overbae.services.datasets import imports

    dataset, run = create_import(imported_source)
    handoffs = []

    def publish(**kwargs):
        handoffs.append(kwargs)
        imports.publish_handoffs(run.pk)

    monkeypatch.setattr(dataset_tasks.diagnose, "apply_async", publish)
    assert imports.execute(str(run.pk), run.inputs)["status"] == "ok"
    assert len(handoffs) == 1
    task_id = handoffs[0]["task_id"]
    assert imports.claim_diagnosis(dataset.pk, task_id)
    assert not imports.claim_diagnosis(dataset.pk, task_id)


def test_split_handoff_retry_only_publishes_the_unacknowledged_target(imported_source, monkeypatch):
    from overbae.services.datasets import imports

    project, upload_id, _filename, _deliveries = imported_source
    train, evaluation = dispatch.create_split(
        project=project,
        user=None,
        name="Pair",
        source={"uploads": [upload_id]},
        eval_percent=50,
        position="head",
    )
    run = DatasetImport.objects.get(dataset=train)
    handoffs = []

    def publish(**kwargs):
        handoffs.append(kwargs)
        if kwargs["kwargs"]["dataset_id"] == str(evaluation.pk):
            raise ConnectionError("broker disconnected after the first split target")

    monkeypatch.setattr(dataset_tasks.diagnose, "apply_async", publish)
    assert imports.execute(str(run.pk), run.inputs)["status"] == "ok"
    assert len(handoffs) == 2
    run.refresh_from_db()
    assert run.handoff_pending
    DatasetImport.objects.filter(pk=run.pk).update(
        handoff_lease_until=timezone.now() - timedelta(seconds=1)
    )
    monkeypatch.setattr(
        dataset_tasks.diagnose, "apply_async", lambda **kwargs: handoffs.append(kwargs)
    )
    imports.publish_handoffs(run.pk)
    assert [item["kwargs"]["dataset_id"] for item in handoffs] == [
        str(train.pk),
        str(evaluation.pk),
        str(evaluation.pk),
    ]
    assert handoffs[1]["task_id"] == handoffs[2]["task_id"]
    run.refresh_from_db()
    assert not run.handoff_pending


def test_receipt_adoption_cannot_be_overwritten_by_a_stale_reaper(imported_source):
    import queue
    import time
    from concurrent.futures import ThreadPoolExecutor

    from django.db import connection, transaction

    from overbae.services.datasets import imports

    project, upload_id, _filename, _deliveries = imported_source
    dataset = Dataset.objects.create(project=project, name="Legacy queued")
    Dataset.objects.filter(pk=dataset.pk).update(updated_at=timezone.now() - timedelta(minutes=66))
    backend_ids = queue.Queue()

    def reap_on_another_connection():
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_backend_pid()")
                backend_ids.put(cursor.fetchone()[0])
            return dataset_tasks.reap_stuck_runs()
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=1) as executor:
        with transaction.atomic():
            Dataset.objects.select_for_update().get(pk=dataset.pk)
            future = executor.submit(reap_on_another_connection)
            backend_id = backend_ids.get(timeout=5)
            deadline = time.monotonic() + 5
            while not future.done():
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_blocking_pids(%s)", [backend_id])
                    if cursor.fetchone()[0]:
                        break
                assert time.monotonic() < deadline, (
                    "The watchdog neither finished nor reached the held row"
                )
                time.sleep(0.01)
            inputs = {
                "dataset_id": str(dataset.pk),
                "source": {"uploads": [upload_id]},
                "user_id": None,
                "infer_capability": True,
            }
            imports.queue_landing_receipt(dataset, uuid.uuid4(), inputs, published=True)
        future.result(timeout=5)
    dataset.refresh_from_db()
    assert dataset.state == "landing" and dataset.error == ""


def test_late_broker_ack_cannot_acknowledge_a_resumed_import(imported_source, monkeypatch):
    from overbae.services.datasets import imports

    project, upload_id, _filename, deliveries = imported_source

    def publish(**kwargs):
        deliveries.append(kwargs)
        if len(deliveries) == 2:
            raise ConnectionError("resumed import was not acknowledged")
        run = DatasetImport.objects.get(pk=kwargs["task_id"])
        claim = imports.claim(run.pk)
        imports.fail(claim, "The delivered worker stopped.")
        imports.resume(run.pk)
        # The first message's acknowledgment arrives after the resumed publish failed.

    monkeypatch.setattr(dataset_tasks.land, "apply_async", publish)
    dataset = dispatch.create_dataset(
        project=project, user=None, name="Delayed ack", source={"uploads": [upload_id]}
    )
    run = DatasetImport.objects.get(dataset=dataset)
    assert len(deliveries) == 2
    assert run.state == "queued" and run.published_at is None
    assert run.failure_code == "dispatch_failed"
