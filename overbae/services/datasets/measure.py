"""Everything a version records about its frame."""

from __future__ import annotations

from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import alignment, contract, paths, store


def describe_frame(dataset, path, *, report=None):
    frame = store.Frame(path)
    report = report or contract.measure(frame)
    null_rates = {c["name"]: c["null_rate"] for c in store.column_stats(path)}
    capability_report = (
        alignment.capability_contract(dataset.capability, frame, dataset.intent)
        if dataset.capability_id is not None
        else {}
    )
    return {
        "rows": len(frame),
        "columns": [{**c, "null_rate": null_rates.get(c["name"], 0.0)} for c in frame.manifest],
        "fingerprint": store.file_sha256(path),
        "intent_report": {
            "train": report["train"],
            "eval": report["eval"],
            "decision": report["decision"],
        },
        "capability_report": capability_report,
        "stats": contract.stats(frame),
        "updated_at": timezone.now(),
    }


def capability_only(dataset: Dataset) -> None:
    """Re-measure the capability contract on every version after the capability or the
    intent changed."""
    for cell in dataset.cells.all():
        path = paths.cell_path(dataset.id, cell.id)
        if not path.exists():
            continue
        report = (
            alignment.capability_contract(dataset.capability, store.Frame(path), dataset.intent)
            if dataset.capability_id is not None
            else {}
        )
        Cell.objects.filter(pk=cell.pk).update(capability_report=report)
