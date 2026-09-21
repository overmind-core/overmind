"""Edits to the chain and the dataset's settings, and what may be deleted."""

from __future__ import annotations

import shutil
from typing import Any

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import measure, paths, store
from overbae.services.datasets.context import context_fingerprint


class DatasetError(ValueError):
    """A rule the caller broke, phrased for the user. ``code`` names it for the API."""

    def __init__(self, detail: str, *, code: str = "dataset_rule") -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code


def enter_busy(dataset_id: Any, state: str, *, from_states: list[str]) -> bool:
    """Claim the dataset for a landing, a run or a turn. ``updated_at`` moves
    with the claim because the reaper measures a busy state's age from it."""
    return bool(
        Dataset.objects.filter(pk=dataset_id, state__in=from_states).update(
            state=state, error="", updated_at=timezone.now()
        )
    )


def _refuse_while_busy(dataset: Dataset) -> None:
    if dataset.state == Dataset.State.RUNNING:
        raise DatasetError("The notebook is running. Wait for it to finish.", code="running")
    if dataset.state == Dataset.State.LANDING:
        raise DatasetError("The source is still landing.", code="landing")


def _refuse_frozen(dataset: Dataset, cell: Cell) -> None:
    if cell.is_source:
        raise DatasetError("The source cannot change.", code="source")
    if cell.frozen:
        raise DatasetError(
            f"{dataset.versions().get(cell.id, cell.title)} was used and is frozen. "
            "Add a cell after it.",
            code="frozen",
        )


def _touch(dataset: Dataset, **fields: Any) -> None:
    Dataset.objects.filter(pk=dataset.pk).update(**fields, updated_at=timezone.now())
    for k, v in fields.items():
        setattr(dataset, k, v)


def _queue_after(dataset: Dataset, position: int) -> None:
    """Cells after ``position`` read a frame that changed: they run again."""
    dataset.cells.filter(position__gt=position).exclude(state=Cell.State.PROPOSED).update(
        state=Cell.State.QUEUED, error=""
    )


@transaction.atomic
def add_cell(
    dataset: Dataset,
    *,
    title: str,
    script: str,
    note: str = "",
    proposed: bool = False,
    user: Any = None,
) -> Cell:
    """A new cell at the end of the chain. A proposal goes after every other
    proposal; a real cell goes before the first proposal."""
    _refuse_while_busy(dataset)
    chain = dataset.chain
    if not chain:
        raise DatasetError("Land a source first.", code="no_source")
    first_proposal = next((c.position for c in chain if c.state == Cell.State.PROPOSED), None)
    if proposed or first_proposal is None:
        position = chain[-1].position + 1
    else:
        position = first_proposal
        for cell in reversed([c for c in chain if c.position >= position]):
            Cell.objects.filter(pk=cell.pk).update(position=F("position") + 1)
    cell = Cell.objects.create(
        dataset=dataset,
        position=position,
        title=title.strip()[:255] or "Step",
        script=script,
        note=note.strip()[:512],
        state=Cell.State.PROPOSED if proposed else Cell.State.QUEUED,
        created_by=user if getattr(user, "pk", None) else None,
    )
    if dataset.state == Dataset.State.ERROR:
        _touch(dataset, state=Dataset.State.IDLE, error="")
    return cell


@transaction.atomic
def edit_cell(
    dataset: Dataset,
    cell: Cell,
    *,
    script: str | None = None,
    title: str | None = None,
    note: str | None = None,
) -> Cell:
    _refuse_while_busy(dataset)
    if cell.review.get("kind") == "synthetic" and script is not None:
        raise DatasetError(
            "Synthetic examples are a recorded batch. Add a transformation cell after it."
        )
    fields: dict[str, Any] = {}
    if title is not None:
        fields["title"] = title.strip()[:255] or cell.title
    if note is not None:
        fields["note"] = note.strip()[:512]
    if script is not None and script != cell.script:
        _refuse_frozen(dataset, cell)
        fields["script"] = script
        fields["review"] = {}
        fields["quality_report"] = {}
        if cell.state != Cell.State.PROPOSED:
            fields["state"] = Cell.State.QUEUED
            fields["error"] = ""
            _queue_after(dataset, cell.position)
    if fields:
        Cell.objects.filter(pk=cell.pk).update(**fields, updated_at=timezone.now())
        cell.refresh_from_db()
    if dataset.state == Dataset.State.ERROR and "script" in fields:
        _touch(dataset, state=Dataset.State.IDLE, error="")
    return cell


@transaction.atomic
def remove_cell(dataset: Dataset, cell: Cell) -> None:
    _refuse_while_busy(dataset)
    _refuse_frozen(dataset, cell)
    position, proposed = cell.position, cell.state == Cell.State.PROPOSED
    path = paths.cell_path(dataset.id, cell.id)
    if dataset.active_id == cell.id:
        _touch(dataset, active=None)
    cell.delete()
    transaction.on_commit(lambda: path.unlink(missing_ok=True))
    for later in dataset.cells.filter(position__gt=position).order_by("position"):
        Cell.objects.filter(pk=later.pk).update(position=F("position") - 1)
    if not proposed:
        _queue_after(dataset, position - 1)
    if dataset.state == Dataset.State.ERROR:
        _touch(dataset, state=Dataset.State.IDLE, error="")


@transaction.atomic
def discard_proposal(dataset: Dataset, cell_id: Any) -> None:
    dataset = Dataset.objects.select_for_update().get(pk=dataset.pk)
    cell = dataset.cells.filter(pk=cell_id).first()
    if cell is None or cell.state != Cell.State.PROPOSED:
        raise DatasetError(
            "Only a pending proposal can be discarded. Applied versions are preserved.",
            code="not_proposal",
        )
    remove_cell(dataset, cell)


@transaction.atomic
def accept_proposal(dataset: Dataset, cell: Cell) -> Cell:
    """Move a proposal to the end of the real chain and queue it."""
    _refuse_while_busy(dataset)
    if cell.state != Cell.State.PROPOSED:
        return cell
    chain = dataset.chain
    if cell.review:
        previous = next((c for c in reversed(chain) if c.state != Cell.State.PROPOSED), None)
        report = cell.review
        path = paths.cell_path(dataset.id, cell.id)
        if (
            previous is None
            or not previous.ran
            or previous.fingerprint != report.get("input_fingerprint")
            or dataset.intent != report.get("intent")
            or context_fingerprint(dataset.capability) != report.get("context_fingerprint")
            or not path.exists()
            or store.file_sha256(path) != report.get("output_fingerprint")
        ):
            raise DatasetError(
                "The proposal is stale. Ask for a new preview against the current data.",
                code="stale_proposal",
            )
        Cell.objects.filter(pk=cell.pk).update(
            review={**report, "status": "accepted", "accepted_at": timezone.now().isoformat()}
        )
    target = next((c.position for c in chain if c.state == Cell.State.PROPOSED), cell.position)
    if target != cell.position:
        # Positions have both a non-negative check and a dataset-local unique constraint.
        Cell.objects.filter(pk=cell.pk).update(position=chain[-1].position + 1)
        for other in reversed([c for c in chain if target <= c.position < cell.position]):
            Cell.objects.filter(pk=other.pk).update(position=F("position") + 1)
        Cell.objects.filter(pk=cell.pk).update(position=target)
    Cell.objects.filter(pk=cell.pk).update(state=Cell.State.QUEUED, updated_at=timezone.now())
    cell.refresh_from_db()
    return cell


def set_active(dataset: Dataset, cell: Cell | None) -> Dataset:
    _refuse_while_busy(dataset)
    if cell is not None and cell.dataset_id != dataset.id:
        raise DatasetError("That version belongs to another dataset.", code="cell_mismatch")
    if cell is not None and not cell.ran:
        raise DatasetError("That version has not run.", code="not_ran")
    _touch(dataset, active=cell)
    return dataset


def set_intent(dataset: Dataset, intent: str) -> Dataset:
    if intent not in (Dataset.Intent.TRAIN, Dataset.Intent.EVAL):
        raise DatasetError("The intent is train or eval.", code="intent")
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
    if dataset.state == Dataset.State.RUNNING:
        return "The notebook is running. Wait for it to finish."
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
