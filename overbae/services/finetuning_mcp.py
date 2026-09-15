"""Neutral fine-tuning orchestration used by MCP."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from overbae.api.serializers import FinetuningJobSerializer
from overbae.models import Capability, Dataset, EvalSet, FinetuningJob


class FineTuneDispatchError(RuntimeError):
    """Raised when the job row exists but its worker could not be queued."""


def launch_finetune(
    *,
    user,
    project,
    dataset: Dataset,
    capability: Capability,
    eval_dataset: Dataset,
    eval_set: EvalSet,
    base_model: str,
    hyperparameters: dict[str, Any],
    name: str,
    use_case: str,
    validation_enabled: bool,
    validation_split_ratio: float,
    validation_dataset: Dataset | None,
    split_method: str,
    group_id: str,
    cell=None,
    validation_cell=None,
) -> FinetuningJob:
    payload: dict[str, Any] = {
        "project": str(project.id),
        "dataset": str(dataset.id),
        "capability": str(capability.id),
        "eval_dataset": str(eval_dataset.id),
        "eval_set": str(eval_set.id),
        "base_model": base_model,
        "name": name,
        "use_case": use_case,
        "hyperparameters": hyperparameters,
        "group_id": group_id,
        "validation_enabled": validation_enabled,
        "validation_split_ratio": validation_split_ratio,
        "split_method": split_method,
    }
    if cell is not None:
        payload["cell"] = str(cell.id)
    if validation_dataset is not None:
        payload["validation_dataset"] = str(validation_dataset.id)
    if validation_cell is not None:
        payload["validation_cell"] = str(validation_cell.id)

    serializer = FinetuningJobSerializer(
        data=payload,
        context={"request": SimpleNamespace(user=user)},
    )
    serializer.is_valid(raise_exception=True)
    job = serializer.save(triggered_by=user)

    from overbae.tasks.finetuning import run_finetuning

    try:
        result = run_finetuning.apply_async(kwargs={"job_id": str(job.id)})
    except Exception as exc:  # noqa: BLE001 — preserve the REST failure state safely
        FinetuningJob.objects.filter(pk=job.pk).update(
            status=FinetuningJob.Status.FAILED,
            error_message="Could not queue the job.",
        )
        raise FineTuneDispatchError from exc
    FinetuningJob.objects.filter(pk=job.pk).update(celery_task_id=result.id)
    job.refresh_from_db()
    return job
