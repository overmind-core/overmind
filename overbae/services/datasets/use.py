"""Pick a readable version and freeze it."""

from __future__ import annotations

import logging
from typing import Any

from django.db import transaction
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import rows
from overbae.services.datasets.contract import public_intent
from overbae.services.datasets.lifecycle import DatasetError

logger = logging.getLogger(__name__)


def check(dataset: Dataset, intent: str, *, cell: Cell | None = None) -> Cell:
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
        if dataset.state == Dataset.State.LANDING:
            raise DatasetError(f"{dataset.name} is still landing.", code="landing")
        if dataset.state == Dataset.State.ERROR:
            raise DatasetError(
                f"The source of {dataset.name} did not land: {dataset.error}", code="land_failed"
            )
        raise DatasetError(f"{dataset.name} has no version.", code="no_version")
    ok, reason = cell.fits(intent)
    if not ok:
        raise DatasetError(f"{dataset.name} · {cell.label}: {reason}", code="contract")
    try:
        rows.verify(cell)
    except (ValueError, rows.RowStoreError) as exc:
        logger.exception("Could not verify dataset cell %s", cell.pk)
        raise DatasetError(
            f"{dataset.name} · {cell.label} is unreadable or has changed.",
            code="workshop_validation",
        ) from exc
    return cell


@transaction.atomic
def use(dataset: Dataset, intent: str, *, cell: Cell | None = None) -> Cell:
    # Share generation's lock so a consumed version cannot change between batches.
    dataset = (
        Dataset.objects.select_for_update(of=("self",))
        .select_related("capability")
        .get(pk=dataset.pk)
    )
    cell = check(dataset, intent, cell=cell)
    freeze(cell)
    return cell


def freeze(*cells: Cell | None) -> None:
    for cell in cells:
        if cell is not None and cell.used_at is None:
            Cell.objects.filter(pk=cell.pk, used_at__isnull=True).update(used_at=timezone.now())
            cell.refresh_from_db()


def describe(cell: Cell | None) -> dict[str, Any] | None:
    if cell is None:
        return None
    return {
        "id": str(cell.id),
        "version": cell.label,
        "title": cell.title,
        "rows": cell.rows,
        "fingerprint": cell.fingerprint,
        "dataset_id": str(cell.dataset_id),
    }
