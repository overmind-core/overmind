"""Everything a cell records about the frame it left."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import alignment, contract, proposals, store
from overbae.services.datasets.profile import profile_records


def frame(
    dataset: Dataset,
    cell: Cell,
    path: Path,
    *,
    df: pd.DataFrame | None = None,
    report: dict[str, Any] | None = None,
    **fields,
) -> Cell:
    """``df`` and ``report`` spare a second read and a second measure when the
    caller already holds them for ``path``."""
    report = report or (contract.measure(df) if df is not None else contract.measure_path(path))
    manifest = store.read_manifest(path)
    null_rates = store.null_rates(path)
    capability_report = (
        capability_path(dataset.capability, path, dataset.intent)
        if dataset.capability_id is not None
        else {}
    )
    fingerprint = store.file_sha256(path)
    if cell.preparation_plan:
        fields["preparation_plan"] = {**cell.preparation_plan, "result_fingerprint": fingerprint}
    Cell.objects.filter(pk=cell.pk).update(
        state=Cell.State.OK,
        error="",
        rows=store.row_count(path),
        columns=[{**c, "null_rate": null_rates.get(c["name"], 0.0)} for c in manifest],
        fingerprint=fingerprint,
        intent_report={"train": report["train"], "eval": report["eval"]},
        capability_report=capability_report,
        stats={
            **contract.stats_rows(store.iter_rows(path), {c["name"] for c in manifest}),
            "preparation_profile": profile_records(store.iter_rows(path)),
        },
        updated_at=timezone.now(),
        **fields,
    )
    cell.refresh_from_db()
    proposals.retire_outdated(dataset)
    from overbae.services.eval.eval_set import maybe_enqueue_card_evaluator_sync

    maybe_enqueue_card_evaluator_sync(dataset)
    return cell


def capability_path(capability, path, intent):
    report = {}
    for df in store.iter_frames(path):
        part = alignment.capability_contract(capability, df, intent)
        if not report:
            report = part
            continue
        report["rows"] += part.get("rows", 0)
        report["rows_ok"] += part.get("rows_ok", 0)
        report["ok"] = report["ok"] and part.get("ok", False)
        if not report["ok"]:
            report["reason"] = (
                f"{report['rows'] - report['rows_ok']} of {report['rows']} rows "
                "do not match the capability."
            )
    return report


def capability_only(dataset: Dataset) -> None:
    """Re-measure the capability contract on every cell that ran, after the
    capability or the intent changed."""
    from overbae.services.datasets import paths

    for cell in dataset.cells.filter(state=Cell.State.OK):
        path = paths.cell_path(dataset.id, cell.id)
        if not path.exists():
            continue
        report = (
            capability_path(dataset.capability, path, dataset.intent)
            if dataset.capability_id is not None
            else {}
        )
        Cell.objects.filter(pk=cell.pk).update(capability_report=report)
