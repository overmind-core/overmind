from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from overbae.models import Dataset, DatasetImport
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
    receipts = list(
        DatasetImport.objects.select_for_update().filter(
            Q(dataset_id=dataset_id) | Q(evaluation_id=dataset_id), state__in=["queued", "running"]
        )
    )
    dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
    if receipts:
        DatasetImport.objects.filter(pk__in=[run.pk for run in receipts]).update(
            state="cancelled", owner=None, lease_until=None, updated_at=timezone.now()
        )
        sibling_ids = {
            pk
            for run in receipts
            for pk in (run.dataset_id, run.evaluation_id)
            if pk is not None and pk != dataset.pk
        }
        for sibling in Dataset.objects.select_for_update().filter(pk__in=sibling_ids):
            sibling.state = Dataset.State.ERROR
            sibling.error = "The paired source import was cancelled. Attach a source to retry."
            sibling.operation = {
                **sibling.operation,
                "state": "cancelled",
                "finished_at": timezone.now().isoformat(),
            }
            sibling.save(update_fields=["state", "error", "operation", "updated_at"])
    operation = {
        **dataset.operation,
        "state": "cancelled",
        "cancelled_at": timezone.now().isoformat(),
    }
    fields = {"operation": operation}
    if receipts or dataset.operation.get("state") != "running":
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
