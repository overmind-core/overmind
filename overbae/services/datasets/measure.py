"""Everything a cell records about the frame it left."""

from __future__ import annotations

from pathlib import Path

from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import alignment, contract, store


def frame(dataset: Dataset, cell: Cell, path: Path, **fields) -> Cell:
    df = store.read_frame(path)
    report = contract.measure(df)
    manifest = store.read_manifest(path)
    null_rates = {c["name"]: c["null_rate"] for c in store.column_stats(path)}
    capability_report = (
        alignment.capability_contract(dataset.capability, df, dataset.intent)
        if dataset.capability_id is not None
        else {}
    )
    Cell.objects.filter(pk=cell.pk).update(
        state=Cell.State.OK,
        error="",
        rows=int(len(df)),
        columns=[{**c, "null_rate": null_rates.get(c["name"], 0.0)} for c in manifest],
        fingerprint=store.file_sha256(path),
        intent_report={"train": report["train"], "eval": report["eval"]},
        capability_report=capability_report,
        stats=contract.stats(df),
        updated_at=timezone.now(),
        **fields,
    )
    cell.refresh_from_db()
    from overbae.services.eval.eval_set import maybe_enqueue_card_evaluator_sync

    maybe_enqueue_card_evaluator_sync(dataset)
    return cell


def capability_only(dataset: Dataset) -> None:
    """Re-measure the capability contract on every cell that ran, after the
    capability or the intent changed."""
    from overbae.services.datasets import paths

    for cell in dataset.cells.filter(state=Cell.State.OK):
        path = paths.cell_path(dataset.id, cell.id)
        if not path.exists():
            continue
        report = (
            alignment.capability_contract(
                dataset.capability, store.read_frame(path), dataset.intent
            )
            if dataset.capability_id is not None
            else {}
        )
        Cell.objects.filter(pk=cell.pk).update(capability_report=report)
