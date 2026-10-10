from django.db import transaction
from django.utils import timezone

from overbae.models import Dataset
from overbae.services.datasets.lifecycle import DatasetError


@transaction.atomic
def started(dataset_id, task_id, *, attachment_request=""):
    dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
    if dataset.source_spec.get("attachment_request", "") != attachment_request:
        return None
    old = dataset.operation
    if old.get("state") == "cancelled" or dataset.state != Dataset.State.LANDING:
        return None
    if old.get("state") == "running" or (
        task_id and old.get("task_id") == task_id and old.get("state") == "completed"
    ):
        return None
    operation = {
        "task_id": task_id,
        "state": "running",
        "started_at": timezone.now().isoformat(),
    }
    Dataset.objects.filter(pk=dataset_id).update(operation=operation)
    return operation


def cancellation_requested(dataset_id):
    return Dataset.objects.filter(pk=dataset_id, operation__state="cancelled").exists()


def check_cancelled(dataset_id):
    if cancellation_requested(dataset_id):
        raise DatasetError("Source import was cancelled.", code="cancelled")


@transaction.atomic
def cancel(dataset_id):
    dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
    operation = {
        **dataset.operation,
        "state": "cancelled",
        "cancelled_at": timezone.now().isoformat(),
    }
    fields = {"operation": operation}
    if dataset.operation.get("state") != "running":
        fields.update(state=Dataset.State.IDLE, error="")
    Dataset.objects.filter(pk=dataset_id).update(**fields)
    return operation


@transaction.atomic
def finished(dataset_id, *, task_id=None):
    dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
    if task_id is not None and dataset.operation.get("task_id") != task_id:
        return dataset.operation
    cancelled = dataset.operation.get("state") == "cancelled"
    operation = {
        **dataset.operation,
        "state": "cancelled" if cancelled else "completed",
        "finished_at": timezone.now().isoformat(),
    }
    fields = {"operation": operation}
    if cancelled:
        fields.update(state=Dataset.State.IDLE, error="")
    Dataset.objects.filter(pk=dataset_id).update(**fields)
    return operation
