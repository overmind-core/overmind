"""Run the chain: every cell after the source, in position order. A cell whose
script and input are unchanged keeps its frame; the first failure stops the
run and leaves the cells after it queued."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import measure, paths, store
from overbae.services.datasets.notebook import events, runner

logger = logging.getLogger(__name__)


def _emit(dataset: Dataset, event: dict[str, Any]) -> dict[str, Any]:
    payload = {"dataset_id": str(dataset.id), **event}
    events.publish(dataset.id, payload)
    return payload


def _set(dataset: Dataset, **fields: Any) -> None:
    Dataset.objects.filter(pk=dataset.pk).update(**fields, updated_at=timezone.now())
    for k, v in fields.items():
        setattr(dataset, k, v)


def _cell_event(cell: Cell, versions: dict[Any, str]) -> dict[str, Any]:
    return {
        "cell_id": str(cell.id),
        "position": cell.position,
        "version": versions.get(cell.id, ""),
        "state": cell.state,
        "rows": cell.rows,
        "error": cell.error,
    }


def stale(cell: Cell, previous: Cell) -> bool:
    return (
        cell.state != Cell.State.OK
        or not cell.fingerprint
        or cell.input_fingerprint != previous.fingerprint
        or not paths.cell_path(cell.dataset_id, cell.id).exists()
    )


def execute(dataset: Dataset, *, user: Any = None) -> Dataset:
    for _event in iter_execute(dataset, user=user):
        pass
    return dataset


def iter_execute(dataset: Dataset, *, user: Any = None) -> Iterator[dict[str, Any]]:
    """:func:`execute` as a generator: yields every SSE event as it happens."""
    chain = [c for c in dataset.chain if c.state != Cell.State.PROPOSED]
    source = chain[0] if chain and chain[0].position == 0 else None
    if source is None or not source.ran:
        _set(dataset, state=Dataset.State.ERROR, error="The source has not landed.")
        yield _emit(dataset, {"type": "run_failed", "error": dataset.error})
        return
    _set(dataset, state=Dataset.State.RUNNING, error="")
    versions = dataset.versions()
    yield _emit(dataset, {"type": "run_started", "cells": [str(c.id) for c in chain[1:]]})
    cache = paths.library_cache(dataset.project_id)
    previous = source
    failed: Cell | None = None
    for cell in chain[1:]:
        if failed is not None:
            if cell.state != Cell.State.QUEUED:
                Cell.objects.filter(pk=cell.pk).update(state=Cell.State.QUEUED)
            continue
        if not stale(cell, previous):
            yield _emit(
                dataset, {"type": "cell_done", **_cell_event(cell, versions), "cached": True}
            )
            previous = cell
            continue
        if cell.used_at is not None:
            failed = cell
            Cell.objects.filter(pk=cell.pk).update(
                state=Cell.State.FAILED, error="This version was used and cannot change."
            )
            yield _emit(
                dataset, {"type": "cell_failed", "cell_id": str(cell.id), "error": cell.error}
            )
            continue
        Cell.objects.filter(pk=cell.pk).update(state=Cell.State.RUNNING, error="")
        cell.state = Cell.State.RUNNING
        yield _emit(dataset, {"type": "cell_started", **_cell_event(cell, versions)})
        started = timezone.now()
        result = runner.run(
            cell.script, paths.cell_path(dataset.id, previous.id), library_cache=cache
        )
        if result.frame is None:
            failed = cell
            error = result.error or "The cell produced no frame."
            Cell.objects.filter(pk=cell.pk).update(
                state=Cell.State.FAILED, error=error, updated_at=timezone.now()
            )
            cell.state, cell.error = Cell.State.FAILED, error
            yield _emit(dataset, {"type": "cell_failed", **_cell_event(cell, versions)})
            continue
        out_path = paths.cell_path(dataset.id, cell.id)
        store.write_frame(out_path, result.frame)
        measure.frame(
            dataset,
            cell,
            out_path,
            input_fingerprint=previous.fingerprint,
            seconds=round((timezone.now() - started).total_seconds(), 2),
        )
        yield _emit(dataset, {"type": "cell_done", **_cell_event(cell, versions)})
        previous = cell

    if failed is not None:
        _set(dataset, state=Dataset.State.ERROR, error=f"{failed.title}: {failed.error}"[:4000])
        yield _emit(
            dataset, {"type": "run_failed", "cell_id": str(failed.id), "error": dataset.error}
        )
        return
    _set(dataset, state=Dataset.State.IDLE)
    active = dataset.active_cell
    yield _emit(
        dataset,
        {
            "type": "run_done",
            "cell_id": str(active.id) if active else "",
            "version": versions.get(active.id, "") if active else "",
            "rows": active.rows if active else 0,
        },
    )


def try_script(dataset: Dataset, script: str, *, after: Cell) -> runner.CellResult:
    """Run a script against ``after``'s frame without landing a cell."""
    return runner.run(
        script,
        paths.cell_path(dataset.id, after.id),
        library_cache=paths.library_cache(dataset.project_id),
    )
