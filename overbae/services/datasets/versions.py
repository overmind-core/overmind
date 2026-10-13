"""Versions are immutable cells in a tree; each has one parent, the source has none."""

from __future__ import annotations

import re
import uuid
from typing import Any

from django.db import transaction
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import events
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.eval.eval_set import maybe_enqueue_card_evaluator_sync

_LABEL = re.compile(r"^v?(\d+)$", re.IGNORECASE)


def resolve_cell(dataset: Dataset, ref: Any, *, field: str = "version") -> Cell:
    """A version by label (``v3`` or ``3``) or id; blank means the active one."""
    ref = str(ref or "").strip()
    if not ref:
        if dataset.active_id is None:
            raise DatasetError("The dataset has no version yet.", code="no_version", field=field)
        return dataset.cells.get(pk=dataset.active_id)
    cell = None
    match = _LABEL.match(ref)
    if match:
        cell = dataset.cells.filter(position=int(match.group(1))).first()
    else:
        try:
            cell = dataset.cells.filter(pk=uuid.UUID(ref)).first()
        except ValueError:
            cell = None
    if cell is None:
        raise DatasetError(f"No version {ref}.", code="no_cell", field=field)
    return cell


def activate(dataset: Dataset, cell: Cell, *, expected_active: Cell | None = None) -> Dataset:
    with transaction.atomic():
        locked = Dataset.objects.select_for_update().get(pk=dataset.pk)
        if locked.state == Dataset.State.LANDING:
            raise DatasetError("The source is still landing.", code="landing")
        if cell.dataset_id != locked.pk:
            raise DatasetError("That version belongs to another dataset.", code="cell_mismatch")
        if expected_active is not None and locked.active_id != expected_active.pk:
            current = locked.cells.filter(pk=locked.active_id).first()
            raise DatasetError(
                f"The active version is {current.label if current else 'not set'}, "
                f"not {expected_active.label}.",
                code="active_conflict",
            )
        if locked.active_id != cell.pk:
            Dataset.objects.filter(pk=locked.pk).update(active=cell, updated_at=timezone.now())
            transaction.on_commit(
                lambda: events.publish(
                    locked.pk,
                    {
                        "dataset_id": str(locked.pk),
                        "type": "dataset_updated",
                        "changed": {"active": str(cell.pk)},
                    },
                )
            )
    dataset.refresh_from_db()
    maybe_enqueue_card_evaluator_sync(dataset)
    return dataset
