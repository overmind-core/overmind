"""The one gate consumers pass. ``check`` picks the cell and checks both
contracts; ``use`` also marks it used, and belongs in the transaction that
creates the consumer's row."""

from __future__ import annotations

from typing import Any

from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets.contract import public_intent
from overbae.services.datasets.lifecycle import DatasetError


def check(dataset: Dataset, intent: str, *, cell: Cell | None = None) -> Cell:
    """``intent`` is ``train`` or ``eval``: what the consumer needs. The dataset's
    own intent must agree. Changes nothing."""
    if cell is not None and cell.dataset_id != dataset.id:
        raise DatasetError("That version belongs to another dataset.", code="cell_mismatch")
    stored = public_intent(dataset.intent)
    if stored != intent:
        raise DatasetError(
            f"{dataset.name} is a {stored} dataset; this needs {intent}.", code="intent"
        )
    if cell is None:
        cell = dataset.active_cell
    else:
        cell.refresh_from_db()
    if cell is None:
        if dataset.state == Dataset.State.ERROR:
            raise DatasetError(
                f"The last run of {dataset.name} failed: {dataset.error}", code="run_failed"
            )
        if dataset.state in (Dataset.State.RUNNING, Dataset.State.DIAGNOSING):
            raise DatasetError(f"{dataset.name} is running. Wait for it to finish.", code="running")
        raise DatasetError(f"{dataset.name} has no version that ran.", code="no_version")
    ok, reason = cell.fits(intent)
    if not ok:
        raise DatasetError(f"{dataset.name} · {label(dataset, cell)}: {reason}", code="contract")
    return cell


def use(dataset: Dataset, intent: str, *, cell: Cell | None = None) -> Cell:
    """``check``, then set ``used_at`` the first time, which freezes the cell
    and every cell it reads and starts a new major version."""
    cell = check(dataset, intent, cell=cell)
    freeze(cell)
    return cell


def freeze(*cells: Cell | None) -> None:
    for cell in cells:
        if cell is not None and cell.used_at is None:
            Cell.objects.filter(pk=cell.pk, used_at__isnull=True).update(used_at=timezone.now())
            cell.refresh_from_db()


def label(dataset: Dataset, cell: Cell) -> str:
    return dataset.versions().get(cell.id, cell.title)


def describe(cell: Cell | None) -> dict[str, Any] | None:
    if cell is None:
        return None
    return {
        "id": str(cell.id),
        "version": cell.dataset.versions().get(cell.id, ""),
        "title": cell.title,
        "rows": cell.rows,
        "fingerprint": cell.fingerprint,
        "dataset_id": str(cell.dataset_id),
    }
