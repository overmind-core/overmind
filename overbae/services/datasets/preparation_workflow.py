from django.db import transaction
from django.utils import timezone

from overbae.models import (
    Activity,
    Cell,
    Dataset,
    DatasetPipelineRun,
    DatasetPreparation,
    DatasetTransfer,
    Project,
)
from overbae.services import activity
from overbae.services.datasets.lifecycle import DatasetError


def describe(preparation):
    return {
        "id": str(preparation.pk),
        "dataset_id": str(preparation.dataset_id),
        "state": preparation.state,
        "actor_kind": preparation.actor_kind,
        "actor_name": preparation.actor_name,
        "outputs": preparation.outputs,
        "updated_at": preparation.updated_at,
    }


@transaction.atomic
def start(dataset, context, request_key):
    # Serialize request keys across datasets and starts/finishes on this dataset.
    Project.objects.select_for_update().get(pk=dataset.project_id)
    dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
    existing = DatasetPreparation.objects.filter(
        project_id=dataset.project_id, request_key=request_key
    ).first()
    if existing:
        if existing.dataset_id != dataset.pk:
            raise DatasetError(
                "This request key belongs to another dataset.", code="request_key_conflict"
            )
        return existing
    if DatasetPreparation.objects.filter(
        dataset=dataset, state__in=["preparing", "paused"]
    ).exists():
        raise DatasetError(
            "This dataset already has an open preparation. Inspect and resume that preparation.",
            code="preparation_active",
        )
    kind, name = activity.connection_actor(context)
    preparation = DatasetPreparation.objects.create(
        project_id=dataset.project_id,
        dataset=dataset,
        request_key=request_key,
        actor_kind=kind,
        actor_name=name,
        created_by=context.user,
    )
    activity.record(
        key=f"preparation:{preparation.pk}",
        project_id=dataset.project_id,
        user_id=context.user.pk,
        kind="dataset_preparation",
        action="Is preparing",
        actor_kind=kind,
        actor_name=name,
        task_kind="dataset_preparation",
        task_id=preparation.pk,
        status="running",
        assets=[activity.asset("dataset", dataset.pk, dataset.name)],
    )
    return preparation


@transaction.atomic
def update(dataset, preparation_id, state, outputs):
    dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
    preparation = (
        DatasetPreparation.objects.select_for_update()
        .filter(pk=preparation_id, dataset=dataset)
        .first()
    )
    if not preparation:
        raise DatasetError("Preparation not found in this dataset.", code="preparation_not_found")
    outputs = sorted(str(value) for value in outputs)
    if preparation.state in {"completed", "cancelled"}:
        if preparation.state == state and preparation.outputs == outputs:
            return preparation
        raise DatasetError(
            "This preparation has ended. Start a new preparation for further work.",
            code="preparation_ended",
        )
    if state == "completed":
        if (
            not outputs
            or len(outputs) != len(set(outputs))
            or Cell.objects.filter(pk__in=outputs, dataset=dataset, state="ok")
            .exclude(fingerprint="")
            .count()
            != len(outputs)
        ):
            raise DatasetError(
                "Completion requires published output cells from this dataset.",
                code="preparation_outputs",
            )
        if (
            DatasetPipelineRun.objects.filter(
                dataset=dataset, state__in=["queued", "running"]
            ).exists()
            or DatasetTransfer.objects.filter(
                specification__dataset=str(dataset.pk), state="uploading"
            ).exists()
            or dataset.state in {"landing", "running"}
        ):
            raise DatasetError(
                "Wait for dataset jobs and uploads to finish before completing preparation.",
                code="preparation_busy",
            )
    elif outputs:
        raise DatasetError(
            "Only completed preparations accept output cells.", code="preparation_outputs"
        )
    preparation.state = state
    preparation.outputs = outputs
    preparation.save(update_fields=["state", "outputs", "updated_at"])
    status, action = {
        "preparing": ("running", "Is preparing"),
        "paused": ("paused", "Paused preparation of"),
        "completed": ("completed", "Prepared"),
        "cancelled": ("cancelled", "Stopped preparation of"),
    }[state]
    Activity.objects.filter(key=f"preparation:{preparation.pk}").update(
        status=status, action=action, updated_at=timezone.now()
    )
    return preparation
