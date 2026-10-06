"""A delayed automatic diagnosis must not become a later explicit user turn."""

import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from overbae.models import Dataset, DatasetImport, Project
from overbae.services.datasets import dispatch, files, imports
from overbae.tasks import datasets as tasks

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def source_import(monkeypatch):
    project = Project.objects.create(name="Diagnosis ownership", slug=uuid.uuid4().hex)
    upload, _ = files.begin_upload("rows.csv")
    files.append_chunk(upload, 0, b"input,expected_output\nq1,a1\nq2,a2\n")
    monkeypatch.setattr(tasks.land, "apply_async", lambda **kwargs: None)
    handoffs = []
    monkeypatch.setattr(tasks.diagnose, "apply_async", lambda **kwargs: handoffs.append(kwargs))
    monkeypatch.setattr(tasks.turn, "apply_async", lambda **kwargs: None)
    return project, upload, handoffs


def expire_diagnosis(dataset):
    Dataset.objects.filter(pk=dataset.pk).update(
        workshop_queued_at=timezone.now() - timedelta(minutes=61)
    )
    tasks.reap_stuck_runs()
    dataset.refresh_from_db()
    assert dataset.state == "error"


def test_delayed_automatic_diagnosis_cannot_claim_a_new_explicit_turn(source_import):
    project, upload, handoffs = source_import
    dataset = dispatch.create_dataset(
        project=project, user=None, name="Rows", source={"uploads": [upload]}
    )
    receipt = DatasetImport.objects.get(dataset=dataset)
    assert imports.execute(str(receipt.pk), receipt.inputs)["status"] == "ok"
    expire_diagnosis(dataset)
    dispatch.message_agent(dataset, None, "Prepare this source now")
    dataset.refresh_from_db()
    assert dataset.state == "diagnosing"
    assert not imports.claim_diagnosis(dataset.pk, handoffs[0]["task_id"])


def test_explicit_split_turn_retires_only_its_own_automatic_diagnosis(source_import):
    project, upload, handoffs = source_import
    train, evaluation = dispatch.create_split(
        project=project,
        user=None,
        name="Pair",
        source={"uploads": [upload]},
        eval_percent=50,
        position="head",
    )
    receipt = DatasetImport.objects.get(dataset=train)
    assert imports.execute(str(receipt.pk), receipt.inputs)["status"] == "ok"
    expire_diagnosis(train)
    dispatch.message_agent(train, None, "Prepare the training data")
    task_ids = {item["kwargs"]["dataset_id"]: item["task_id"] for item in handoffs}
    assert not imports.claim_diagnosis(train.pk, task_ids[str(train.pk)])
    assert imports.claim_diagnosis(evaluation.pk, task_ids[str(evaluation.pk)])


def test_retrying_source_less_import_keeps_its_automatic_diagnosis(source_import):
    project, upload, handoffs = source_import
    dataset = dispatch.create_dataset(
        project=project, user=None, name="Rows", source={"uploads": [upload]}
    )
    receipt = DatasetImport.objects.get(dataset=dataset)
    claim = imports.claim(receipt.pk)
    imports.fail(claim, "The source worker stopped")
    dataset.refresh_from_db()
    dispatch.run_dataset(dataset, None)
    assert imports.execute(str(receipt.pk), receipt.inputs)["status"] == "ok"
    assert imports.claim_diagnosis(dataset.pk, handoffs[0]["task_id"])
