"""Everything a version records about its frame."""

from __future__ import annotations

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from overbae.models import Cell, Dataset
from overbae.services.datasets import alignment, contract, events, paths, store

ANALYTICS_BATCH = 20
ANALYTICS_LOCK_SECONDS = 600


def describe_frame(dataset, path, *, report=None):
    frame = store.Frame(path)
    report = report or contract.measure(frame)
    column_stats = store.column_stats(path)
    null_rates = {c["name"]: c["null_rate"] for c in column_stats}
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
        "analytics": contract.analytics(frame, column_stats),
        "updated_at": timezone.now(),
    }


def request_analytics(dataset: Dataset, cells: list[Cell]) -> None:
    """Versions measured before analytics existed are measured in the background."""
    if all(cell.analytics for cell in cells):
        return
    # The task imports this service, so task imports stay at dispatch boundaries.
    from overbae.tasks.datasets import measure_analytics

    key = f"dataset-analytics:{dataset.pk}"

    def dispatch():
        if not cache.add(key, 1, timeout=ANALYTICS_LOCK_SECONDS):
            return
        try:
            measure_analytics.delay(dataset_id=str(dataset.pk))
        except Exception:
            cache.delete(key)
            raise

    transaction.on_commit(dispatch)


def backfill_analytics(dataset_id) -> int:
    cells = list(
        Cell.objects.filter(dataset_id=dataset_id, analytics={}).order_by("position")[
            :ANALYTICS_BATCH
        ]
    )
    for cell in cells:
        path = paths.cell_path(dataset_id, cell.id)
        result = (
            contract.analytics(store.Frame(path), store.column_stats(path))
            if path.exists()
            else {"labels": [], "inputs": {}}
        )
        Cell.objects.filter(pk=cell.pk, analytics={}).update(analytics=result)
    cache.delete(f"dataset-analytics:{dataset_id}")
    if cells:
        events.publish(
            dataset_id,
            {"dataset_id": str(dataset_id), "type": "dataset_updated", "changed": {}},
        )
    return len(cells)


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
