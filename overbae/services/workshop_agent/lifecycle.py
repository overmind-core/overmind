import hashlib
import json
from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from overbae.models import (
    Cell,
    DataPartitionMember,
    Dataset,
    DatasetPreparation,
    Project,
    WorkshopAction,
    WorkshopTask,
    WorkshopWorkspace,
)
from overbae.services.datasets import preparation_workflow, use
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.workshop_agent import provisioning

TERMINAL = frozenset({"completed", "cancelled", "failed"})


def completion_outputs(task, outputs):
    selected = {str(value) for value in outputs}
    if not selected or len(selected) != len(outputs):
        raise DatasetError("Completion requires distinct published output cells.")
    cells = dict(
        Cell.objects.filter(pk__in=selected, dataset__project_id=task.project_id, state="ok")
        .exclude(fingerprint="")
        .values_list("pk", "dataset_id")
    )
    primary = {str(pk) for pk, dataset_id in cells.items() if dataset_id == task.dataset_id}
    related = {
        str(value)
        for value in DataPartitionMember.objects.filter(
            cell_id__in=selected - primary,
            plan__source_cell_id__in=primary,
            plan__project_id=task.project_id,
            plan__state="completed",
        ).values_list("cell_id", flat=True)
    }
    if not primary or len(cells) != len(selected) or selected != primary | related:
        raise DatasetError(
            "Include published cells from the task dataset. Additional outputs must be completed "
            "partition members of those cells in the same project."
        )
    return sorted(primary)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def authorise(task, *, require_workspace=True):
    if (
        not task.created_by.is_active
        or not Project.objects.filter(
            pk=task.project_id, is_active=True, memberships__user_id=task.created_by_id
        ).exists()
    ):
        raise DatasetError("Task authority has been revoked.", code="permission_denied")
    if (
        require_workspace
        and not WorkshopWorkspace.objects.filter(
            project_id=task.project_id, enabled=True, sandbox_id__isnull=False
        ).exists()
    ):
        raise DatasetError("The Workshop workspace is not enabled.", code="dataset_invalid")
    if (
        task.preparation_id
        and DatasetPreparation.objects.filter(pk=task.preparation_id, state="cancelled").exists()
    ):
        raise DatasetError("The dataset preparation has ended.", code="dataset_busy")
    if task.desired_state != "running" or task.state in TERMINAL:
        raise DatasetError("The Workshop task is not running.", code="dataset_busy")


@dataclass(frozen=True)
class NativeGrant:
    project: object
    user: object
    write: bool = True
    client_name: str = "Overmind Workshop"

    def has_permission(self, permission):
        return permission == "read" or (permission == "write" and self.write)


def context(task):
    authorise(task)
    return NativeGrant(task.project, task.created_by)


@transaction.atomic
def start(grant, payload, *, record_objective=True):
    Project.objects.select_for_update().get(pk=grant.project.pk)
    dataset = Dataset.objects.filter(pk=payload.dataset, project=grant.project).first()
    if dataset is None:
        raise DatasetError("Dataset not found.", code="dataset_not_found")
    request = payload.model_dump(mode="json", exclude={"request_key"})
    if not request["context_datasets"]:
        request.pop("context_datasets")
    fingerprint = digest(request)
    existing = WorkshopTask.objects.filter(
        project=grant.project, request_key=payload.request_key
    ).first()
    if existing:
        if existing.fingerprint != fingerprint:
            raise DatasetError(
                "Request key conflicts with an existing task.", code="request_key_conflict"
            )
        return existing
    if not grant.has_permission("write"):
        raise DatasetError("Write permission is required.", code="permission_denied")
    provisioning.reserve(grant.project)
    if (
        len(set(payload.context_datasets)) != len(payload.context_datasets)
        or payload.dataset in payload.context_datasets
    ):
        raise DatasetError("Select distinct context datasets, separate from the working dataset.")
    selected = {
        value.pk: value
        for value in Dataset.objects.select_for_update()
        .filter(pk__in=payload.context_datasets, project=grant.project)
        .prefetch_related("cells")
    }
    if len(selected) != len(payload.context_datasets):
        raise DatasetError("Context dataset not found in this project.", code="dataset_not_found")
    context_sources = []
    for identifier in payload.context_datasets:
        reference = selected[identifier]
        cell = reference.active_cell
        if cell is None or cell.state != "ok" or not cell.fingerprint:
            raise DatasetError("Context datasets must have a readable published version.")
        use.freeze(cell)
        context_sources.append(
            {
                "dataset": str(reference.pk),
                "name": reference.name,
                "cell": str(cell.pk),
                "fingerprint": cell.fingerprint,
                "rows": cell.rows,
                "columns": cell.columns,
                "source_spec": reference.source_spec,
            }
        )
    task = WorkshopTask.objects.create(
        project=grant.project,
        dataset=dataset,
        created_by=grant.user,
        objective=payload.objective,
        context_sources=context_sources,
        request_key=payload.request_key,
        fingerprint=fingerprint,
        max_steps=payload.max_steps,
    )
    task.preparation = preparation_workflow.start(
        dataset, NativeGrant(grant.project, grant.user), f"native:{task.pk}"
    )
    task.save(update_fields=["preparation"])
    if record_objective:
        # Imported here because conversation.send starts a task through this service.
        from overbae.services.workshop_agent.conversation import emit

        emit(
            task,
            "objective",
            {"id": "objective", "role": "user", "text": task.objective, "status": "completed"},
        )
    return task


def get(task_id):
    return WorkshopTask.objects.select_related(
        "project", "dataset", "created_by", "preparation"
    ).get(pk=task_id)


@transaction.atomic
def control(project, task_id, command):
    Project.objects.select_for_update().get(pk=project.pk)
    task = WorkshopTask.objects.select_for_update().get(pk=task_id, project=project)
    if command not in {"pause", "resume", "cancel"}:
        raise DatasetError("Unknown task command.", code="dataset_invalid")
    if task.state in TERMINAL:
        return task
    if task.desired_state == "cancelled" and command != "cancel":
        raise DatasetError("Wait for cancellation to finish.", code="dataset_busy")
    # Publication reconciles its retained replay through transactional request keys.
    if (
        command == "resume"
        and task.actions.filter(state__in=["unknown", "intent"])
        .exclude(name="publish_workshop")
        .exists()
    ):
        raise DatasetError("Resolve uncertain actions before resuming.", code="dataset_busy")
    task.desired_state = {"pause": "paused", "resume": "running", "cancel": "cancelled"}[command]
    if command == "resume":
        task.result = {}
    task.save(update_fields=["desired_state", "result", "updated_at"])
    return task


@transaction.atomic
def claim(task_id, sequence, name, arguments):
    task = (
        WorkshopTask.objects.select_for_update()
        .select_related("project", "created_by")
        .get(pk=task_id)
    )
    authorise(task)
    fingerprint = digest({"name": name, "arguments": arguments})
    existing = WorkshopAction.objects.filter(task=task, sequence=sequence).first()
    if existing:
        if existing.fingerprint != fingerprint:
            raise DatasetError("Action sequence conflicts.", code="request_key_conflict")
        return existing, False
    if task.max_steps and sequence >= task.max_steps:
        raise DatasetError("The task action limit was reached.", code="dataset_invalid")
    task.step = sequence
    task.state = "running"
    task.save(update_fields=["step", "state", "updated_at"])
    return WorkshopAction.objects.create(
        task=task, sequence=sequence, name=name, arguments=arguments, fingerprint=fingerprint
    ), True


def record_action(action_id, state, result):
    WorkshopAction.objects.filter(pk=action_id).update(
        state=state, result=result, updated_at=timezone.now()
    )


def describe(task):
    usage_known = all(
        receipt.get("usage_known") is True
        for receipt in task.actions.filter(
            name="reason", state__in=["completed", "unknown"]
        ).values_list("receipt", flat=True)
    )
    return {
        "id": str(task.pk),
        "dataset": str(task.dataset_id),
        "objective": task.objective,
        "context_sources": task.context_sources,
        "state": task.state,
        "desired_state": task.desired_state,
        "step": task.step,
        "used_tokens": task.used_tokens if usage_known else None,
        "result": task.result,
        "updated_at": task.updated_at.isoformat(),
    }
