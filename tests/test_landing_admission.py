import uuid
from datetime import timedelta

import pytest
from django.utils import timezone
from factories import make_project

from overbae.models import Dataset, DatasetLandingWork
from overbae.services.datasets import admission
from overbae.services.datasets.lifecycle import DatasetError
from overbae.tasks.datasets import land

pytestmark = pytest.mark.django_db(transaction=True)


def queue(project):
    dataset = Dataset.objects.create(project=project, name="pending", state="landing")
    inputs = {"dataset_id": str(dataset.pk), "source": {"rows": [{"value": 1}]}}
    return admission.enqueue(inputs, uuid.uuid4())


def test_lost_started_import_is_fenced_without_replay_and_other_projects_keep_capacity(settings):
    settings.DATASET_LANDING_MAX_IN_FLIGHT = 2
    noisy, quiet = make_project(), make_project()
    first, later, other = queue(noisy), queue(noisy), queue(quiet)
    messages = []
    admission.dispatch(lambda **message: messages.append(message))
    assert {x["task_id"] for x in messages} == {str(first.pk), str(other.pk)}
    first.refresh_from_db()
    other.refresh_from_db()
    assert admission.claim(first.pk, first.owner)
    assert not admission.claim(first.pk, first.owner)
    assert admission.claim(other.pk, other.owner)
    DatasetLandingWork.objects.filter(pk=first.pk).update(
        lease_until=timezone.now() - timedelta(seconds=1)
    )
    messages.clear()
    admission.reconcile(lambda **message: messages.append(message))
    first.refresh_from_db()
    other.refresh_from_db()
    later.refresh_from_db()
    assert first.state == "failed" and other.state == "running"
    assert later.state == "admitted" and messages[0]["task_id"] == str(later.pk)
    stale = land.apply(
        kwargs=first.inputs, task_id=str(first.pk), headers={admission.HEADER: str(first.owner)}
    )
    assert stale.get() == {"status": "not_claimed"}
    assert not first.dataset.cells.exists()


def test_unacknowledged_publication_can_recover_but_old_delivery_cannot_claim():
    work = queue(make_project())
    messages = []

    def interrupted(**message):
        messages.append(message)
        raise ConnectionError("acknowledgement lost")

    admission.dispatch(interrupted)
    old = messages[0]
    DatasetLandingWork.objects.filter(pk=work.pk).update(
        lease_until=timezone.now() - timedelta(seconds=1)
    )
    admission.reconcile(lambda **message: messages.append(message))
    assert len(messages) == 2
    assert not admission.claim(work.pk, old["headers"][admission.HEADER])
    assert admission.claim(work.pk, messages[1]["headers"][admission.HEADER])
    work.refresh_from_db()
    admission.finish(work.pk, old["headers"][admission.HEADER])
    work.refresh_from_db()
    assert work.state == "running"


def test_repeated_task_identity_preserves_its_receipt_and_rejects_changed_inputs():
    work = queue(make_project())
    assert admission.enqueue(work.inputs, work.pk).pk == work.pk
    with pytest.raises(DatasetError, match="changed"):
        admission.enqueue({**work.inputs, "source": {"rows": [{"value": 2}]}}, work.pk)
    assert DatasetLandingWork.objects.count() == 1


def test_admission_uses_reported_scaled_capacity_and_retains_the_configured_ceiling(settings):
    from django.core.cache import cache

    settings.DATASET_LANDING_MAX_IN_FLIGHT = 4
    jobs = [queue(make_project()) for _ in range(6)]
    cache.set("queue-capacity:landing:workers", 6, timeout=90)
    messages = []
    admission.dispatch(lambda **message: messages.append(message))
    assert len(messages) == 4
    cache.set("queue-capacity:landing:workers", 2, timeout=90)
    assert admission.dispatch(lambda **message: messages.append(message))["admitted"] == 0
    assert DatasetLandingWork.objects.filter(state="waiting").count() == 2
    assert {x["task_id"] for x in messages}.issubset({str(job.pk) for job in jobs})


def test_only_an_explicit_resumed_source_receipt_can_requeue_terminal_admission():
    from overbae.models import DatasetImport

    work = queue(make_project())
    DatasetLandingWork.objects.filter(pk=work.pk).update(state="failed", owner=uuid.uuid4())
    work.refresh_from_db()
    old_owner = work.owner
    assert admission.enqueue(work.inputs, work.pk).state == "failed"
    DatasetImport.objects.create(
        pk=work.pk,
        dataset=work.dataset,
        inputs=work.inputs,
        state="queued",
        queued_at=timezone.now(),
        published_at=None,
    )
    resumed = admission.enqueue(work.inputs, work.pk)
    assert resumed.state == "waiting" and resumed.owner is None
    messages = []
    admission.dispatch(lambda **message: messages.append(message))
    assert messages[0]["task_id"] == str(work.pk)
    assert not admission.claim(work.pk, old_owner)
    assert admission.claim(work.pk, messages[0]["headers"][admission.HEADER])


def test_split_recovery_skips_publishing_rows_then_stops_both_members_without_replay():
    import threading

    from django.db import close_old_connections, transaction

    project = make_project()
    primary = Dataset.objects.create(project=project, name="train", state="landing")
    evaluation = Dataset.objects.create(project=project, name="eval", state="landing")
    work = admission.enqueue(
        {
            "dataset_id": str(primary.pk),
            "source": {"rows": [{"value": 1}]},
            "split": {"eval_dataset_id": str(evaluation.pk)},
        },
        uuid.uuid4(),
    )
    messages = []
    admission.dispatch(lambda **message: messages.append(message))
    work.refresh_from_db()
    assert admission.claim(work.pk, work.owner)
    DatasetLandingWork.objects.filter(pk=work.pk).update(
        lease_until=timezone.now() - timedelta(seconds=1)
    )
    locked, release = threading.Event(), threading.Event()

    def publication():
        close_old_connections()
        try:
            with transaction.atomic():
                Dataset.objects.select_for_update().get(pk=primary.pk)
                locked.set()
                assert release.wait(10)
        finally:
            close_old_connections()

    thread = threading.Thread(target=publication)
    thread.start()
    assert locked.wait(5)
    try:
        admission.reconcile(lambda **message: messages.append(message))
        work.refresh_from_db()
        assert work.state == "running"
    finally:
        release.set()
        thread.join(timeout=5)
    assert not thread.is_alive()
    admission.reconcile(lambda **message: messages.append(message))
    work.refresh_from_db()
    primary.refresh_from_db()
    evaluation.refresh_from_db()
    assert work.state == "failed"
    assert primary.state == evaluation.state == "error"
    assert primary.operation["state"] == evaluation.operation["state"] == "failed"
    assert len(messages) == 1
    assert not primary.cells.exists() and not evaluation.cells.exists()


def test_publication_allows_its_independent_heartbeat_and_still_rejects_expired_ownership():
    from concurrent.futures import ThreadPoolExecutor

    from celery import shared_task
    from django.db import close_old_connections, connection, transaction

    work = queue(make_project())
    admission.dispatch(lambda **message: None)
    work.refresh_from_db()
    assert admission.claim(work.pk, work.owner)
    deadline = timezone.now() + timedelta(seconds=60)
    DatasetLandingWork.objects.filter(pk=work.pk).update(lease_until=deadline)

    def heartbeat():
        close_old_connections()
        try:
            with connection.cursor() as cursor:
                cursor.execute("SET lock_timeout = '500ms'")
            admission.renew(work.pk, work.owner)
        finally:
            connection.close()

    @shared_task
    def publication():
        with ThreadPoolExecutor(max_workers=1) as pool, transaction.atomic():
            Dataset.objects.select_for_update().get(pk=work.dataset_id)
            admission.check_current()
            pool.submit(heartbeat).result(timeout=5)
            work.refresh_from_db()
            assert work.lease_until > deadline
            admission.check_current()
            DatasetLandingWork.objects.filter(pk=work.pk).update(
                lease_until=timezone.now() - timedelta(seconds=1)
            )
            with pytest.raises(DatasetError, match="ownership expired"):
                admission.check_current()

    publication.apply(task_id=str(work.pk), headers={admission.HEADER: str(work.owner)}).get(
        propagate=True
    )
