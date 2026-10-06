import logging

from cursor_sdk import Client
from django.db import transaction
from django.utils import timezone

from overbae.models import Dataset, WorkshopRun
from overbae.services.datasets import paths
from overbae.services.datasets.lifecycle import DatasetError

logger = logging.getLogger(__name__)


@transaction.atomic
def change(dataset_id, *, owner_id=None, **fields):
    dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
    if owner_id is not None and dataset.operation.get("task_id") != owner_id:
        raise DatasetError("Operation ownership changed.", code="ownership_lost")
    operation = {**dataset.operation, **fields, "updated_at": timezone.now().isoformat()}
    Dataset.objects.filter(pk=dataset_id).update(operation=operation)
    return operation


@transaction.atomic
def started(dataset_id, task_id):
    dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
    if dataset.operation.get("state") == "cancel_pending":
        raise DatasetError(
            "Cancellation is pending provider acknowledgement.", code="cancel_pending"
        )
    old = dataset.operation
    if old.get("state") == "running" or (
        task_id and old.get("task_id") == task_id and old.get("state") in {"completed", "cancelled"}
    ):
        raise DatasetError(
            "This operation already started; reconcile it before retrying.", code="already_started"
        )
    operation = {
        "task_id": task_id,
        "state": "running",
        "local_stopped": False,
        "started_at": timezone.now().isoformat(),
        "attempt": old.get("attempt", 0) + 1,
        "previous": {key: old.get(key) for key in ("task_id", "state", "started_at", "updated_at")},
    }
    Dataset.objects.filter(pk=dataset_id).update(operation=operation)
    return operation


def provider_submitting(dataset_id, task_id, agent_id, *, workspace_scope=""):
    return change(
        dataset_id,
        owner_id=task_id,
        provider={"agent_id": agent_id, "state": "submitting", "workspace_scope": workspace_scope},
    )


def provider_started(dataset_id, task_id, agent_id, run_id, *, workspace_scope=""):
    return change(
        dataset_id,
        owner_id=task_id,
        provider={
            "agent_id": agent_id,
            "run_id": run_id,
            "state": "running",
            "workspace_scope": workspace_scope,
        },
    )


def cancellation_requested(dataset_id):
    return Dataset.objects.filter(
        pk=dataset_id, operation__state__in=["cancel_pending", "cancelled"]
    ).exists()


def check_cancelled(dataset_id, *, task_id=None):
    if task_id is not None:
        operation = Dataset.objects.values_list("operation", flat=True).get(pk=dataset_id)
        if operation.get("task_id") != task_id:
            raise DatasetError("Operation ownership changed.", code="ownership_lost")
    if cancellation_requested(dataset_id):
        raise DatasetError("Cancellation requested.", code="cancel_pending")


def cancel_provider(provider, *, workspace):
    # Detached cancellation with agent_id selects the SDK's cloud transport.
    with Client.launch_bridge(workspace=workspace, state_root=workspace / ".agent") as client:
        run = client.get_run(provider["run_id"], {"runtime": "local", "cwd": str(workspace)})
        if run.agent_id != provider["agent_id"]:
            raise DatasetError("Saved run belongs to a different agent.", code="ownership_lost")
        run.cancel()


def cancel(dataset_id):
    dataset = Dataset.objects.get(pk=dataset_id)
    if dataset.operation.get("state") == "cancelled":
        return dataset.operation
    WorkshopRun.objects.filter(
        dataset_id=dataset_id,
        state__in=["planning", "queued", "running", "paused", "blocked", "partial"],
    ).update(state="cancelled", updated_at=timezone.now())
    change(dataset_id, state="cancel_pending", cancellation_requested_at=timezone.now().isoformat())
    return reconcile(dataset_id)


def reconcile(dataset_id, *, local_stopped=False):
    dataset = Dataset.objects.get(pk=dataset_id)
    operation = dataset.operation
    if operation.get("state") != "cancel_pending":
        return operation
    provider = operation.get("provider") or {}
    if provider.get("state") == "submitting":
        return change(
            dataset_id,
            local_stopped=local_stopped or operation.get("local_stopped", False),
            error="Provider submission identity is unresolved.",
        )
    if provider.get("run_id") and provider.get("state") not in {"cancelled", "completed"}:
        try:
            root = paths.workspace_dir(dataset.pk)
            scope = provider.get("workspace_scope", "")
            if scope:
                target = (root / scope).resolve()
                if target.parent != root.resolve():
                    raise DatasetError(
                        "Saved workspace identity is invalid.", code="ownership_lost"
                    )
                root = target
            cancel_provider(provider, workspace=root)
        except Exception:
            logger.warning(
                "dataset %s: cancellation acknowledgement failed", dataset.pk, exc_info=True
            )
            return change(
                dataset_id,
                local_stopped=local_stopped or operation.get("local_stopped", False),
                error="Provider cancellation acknowledgement pending.",
            )
        operation = change(dataset_id, provider={**provider, "state": "cancelled"}, error="")
    if local_stopped or operation.get("local_stopped"):
        operation = change(dataset_id, state="cancelled", local_stopped=True, error="")
        Dataset.objects.filter(pk=dataset_id, operation__state="cancelled").update(
            state=Dataset.State.IDLE, error="", updated_at=timezone.now()
        )
    return operation


@transaction.atomic
def finished(dataset_id, *, provider_completed=False, task_id=None):
    dataset = Dataset.objects.select_for_update().get(pk=dataset_id)
    if task_id is not None and dataset.operation.get("task_id") != task_id:
        return dataset.operation
    if provider_completed and dataset.operation.get("provider"):
        change(dataset_id, provider={**dataset.operation["provider"], "state": "completed"})
    dataset.refresh_from_db()
    if dataset.operation.get("provider", {}).get("state") in {"running", "submitting"}:
        change(dataset_id, state="cancel_pending")
    dataset.refresh_from_db()
    if dataset.operation.get("state") == "cancel_pending":
        return reconcile(dataset_id, local_stopped=True)
    return change(dataset_id, state="completed", local_stopped=True)
