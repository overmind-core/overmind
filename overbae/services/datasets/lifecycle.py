"""Edits to the chain and the dataset's settings, and what may be deleted."""

from __future__ import annotations

import shutil
from typing import Any

from django.db import transaction
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import measure, paths


class DatasetError(ValueError):
    """A rule the caller broke, phrased for the user. ``code`` names it for the API."""

    def __init__(self, detail: str, *, code: str = "dataset_rule") -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


def enter_busy(dataset_id: Any, state: str, *, from_states: list[str]) -> bool:
    """Claim the dataset for a landing, a transformation. ``updated_at`` moves
    with the claim because the reaper measures a busy state's age from it."""
    return bool(
        Dataset.objects.filter(pk=dataset_id, state__in=from_states).update(
            state=state, error="", updated_at=timezone.now()
        )
    )


def _refuse_while_busy(dataset: Dataset) -> None:
    if dataset.state == Dataset.State.RUNNING:
        raise DatasetError("A transformation is running. Wait for it to finish.", code="running")
    if dataset.state == Dataset.State.LANDING:
        raise DatasetError("The source is still landing.", code="landing")


def _touch(dataset: Dataset, **fields: Any) -> None:
    Dataset.objects.filter(pk=dataset.pk).update(**fields, updated_at=timezone.now())
    for k, v in fields.items():
        setattr(dataset, k, v)


def set_active(dataset: Dataset, cell: Cell | None) -> Dataset:
    _refuse_while_busy(dataset)
    if cell is not None and cell.dataset_id != dataset.id:
        raise DatasetError("That version belongs to another dataset.", code="cell_mismatch")
    if cell is not None and not cell.ran:
        raise DatasetError("That version has not run.", code="not_ran")
    _touch(dataset, active=cell)
    return dataset


def set_intent(dataset: Dataset, intent: str) -> Dataset:
    _refuse_while_busy(dataset)
    if intent not in (Dataset.Intent.TRAIN, Dataset.Intent.EVAL, Dataset.Intent.EXPLORE):
        raise DatasetError("Choose Training, Eval, or Data exploration.", code="intent")
    if dataset.frozen_before >= 0:
        raise DatasetError("A version was used; the intent is fixed.", code="frozen")
    if intent != dataset.intent:
        _touch(dataset, intent=intent)
        measure.capability_only(dataset)
        if intent == Dataset.Intent.EVAL:
            from overbae.services.eval.eval_set import maybe_enqueue_card_evaluator_sync

            maybe_enqueue_card_evaluator_sync(dataset)
    return dataset


def set_capability(dataset: Dataset, capability: Any) -> Dataset:
    _refuse_while_busy(dataset)
    if dataset.frozen_before >= 0:
        raise DatasetError("A version was used; the capability is fixed.", code="frozen")
    if capability is not None and capability.project_id != dataset.project_id:
        raise DatasetError("That capability belongs to another project.", code="capability")
    if getattr(capability, "id", None) != dataset.capability_id:
        _touch(dataset, capability=capability)
        measure.capability_only(dataset)
        from overbae.services.eval.eval_set import maybe_enqueue_card_evaluator_sync

        maybe_enqueue_card_evaluator_sync(dataset)
    return dataset


def rename(dataset: Dataset, name: str) -> Dataset:
    name = name.strip()[:255]
    if name:
        _touch(dataset, name=name)
    return dataset


def usage(cell: Cell) -> dict[str, list[dict[str, Any]]]:
    """Every consumer that used this cell, with enough to link to it."""
    eval_runs = [
        {"id": str(r.id), "name": r.name, "status": r.status, "created_at": r.created_at}
        for r in cell.eval_runs.order_by("-created_at")[:50]
    ]
    jobs = (
        [
            {
                "id": str(j.id),
                "name": j.name,
                "status": j.status,
                "role": "train",
                "created_at": j.created_at,
            }
            for j in cell.finetuning_jobs.order_by("-created_at")[:50]
        ]
        + [
            {
                "id": str(j.id),
                "name": j.name,
                "status": j.status,
                "role": "validation",
                "created_at": j.created_at,
            }
            for j in cell.validation_finetuning_jobs.order_by("-created_at")[:50]
        ]
        + [
            {
                "id": str(j.id),
                "name": j.name,
                "status": j.status,
                "role": "eval",
                "created_at": j.created_at,
            }
            for j in cell.evaluation_finetuning_jobs.order_by("-created_at")[:50]
        ]
    )
    experiments = [
        {"id": str(e.id), "name": e.name, "status": e.status, "created_at": e.created_at}
        for e in cell.optimizer_experiments.order_by("-created_at")[:50]
    ]
    return {"eval_runs": eval_runs, "finetuning_jobs": jobs, "optimizer_experiments": experiments}


def delete_blocked_reason(dataset: Dataset) -> str:
    if dataset.state == Dataset.State.LANDING:
        return "The source is still landing. Cancel it before deleting the dataset."
    if dataset.state == Dataset.State.RUNNING:
        return "A transformation is running. Wait for it to finish."
    if dataset.cells.filter(pipeline_artifacts__isnull=False).exists():
        return "A version is retained as an external transformation artifact. This dataset cannot be deleted."
    used = [c for c in dataset.cells.all() if c.used_at is not None or any(usage(c).values())]
    if used:
        versions = dataset.versions()
        labels = ", ".join(versions.get(c.id, c.title) for c in used)
        return f"{labels} {'is' if len(used) == 1 else 'are'} used by runs, so this dataset cannot be deleted."
    return ""


@transaction.atomic
def delete_dataset(dataset: Dataset) -> None:
    reason = delete_blocked_reason(dataset)
    if reason:
        raise DatasetError(reason, code="dataset_referenced")
    directory = paths.dataset_dir(dataset.id)
    dataset.delete()
    shutil.rmtree(directory, ignore_errors=True)
