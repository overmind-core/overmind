"""Everything a cell records about the frame it left, in one pass over its rows."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Iterable
from pathlib import Path

import pyarrow.parquet as pq
from django.utils import timezone

from modal_shared.decisions import decision_line
from overbae.models import Cell, Dataset
from overbae.services.datasets import alignment, contract, lineage, store
from overbae.services.datasets.analytics import Analytics
from overbae.services.datasets.examples import missing, native_decision

EXAMPLES = 2
EXAMPLE_CHARS = 200
# Rows between progress reports while a frame is measured.
PROGRESS_ROWS = 10_000


def describe_frame(
    dataset: Dataset,
    path: Path,
    *,
    inherited: Iterable[lineage.Keys] | None = None,
    on_progress: Callable[[int], None] | None = None,
) -> dict:
    """The cell fields for the frame at ``path``. Writes its key file: each row's own
    keys and ``inherited``, the keys of the rows it came from, in frame order.
    ``on_progress`` receives the rows measured so far."""
    manifest = store.read_manifest(path)
    names = [column["name"] for column in manifest]
    statistics = contract.RowStatistics(set(names))
    analytics = Analytics()
    examples = {name: [] for name in names if not name.startswith("_overmind_")}
    keys = lineage.Writer(path)
    inherited = iter(inherited) if inherited is not None else itertools.repeat(lineage.NONE)
    capability = []
    # Each batch's decision-column problems, when this pass checked that exact value.
    problems = []

    def observed():
        for count, row in enumerate(store.iter_rows(path)):
            if on_progress and count % PROGRESS_ROWS == 0:
                on_progress(count)
            statistics.observe(row)
            decision = native_decision(row)
            problem = _problem(decision)
            analytics.observe(decision, valid=problem is None)
            if problems is not None:
                if "decision" in row and decision is not row["decision"]:
                    problems_off()
                else:
                    problems.append(problem)
            keys.add(row.get(store.SOURCE_ROW), lineage.own_keys(row) | next(inherited))
            for name, found in examples.items():
                if len(found) < EXAMPLES and not missing(value := row.get(name)):
                    text = value if isinstance(value, str) else store.json_dumps(value)
                    # PostgreSQL JSON cannot hold NUL.
                    text = text.replace("\x00", "\\u0000")
                    if text and text[:EXAMPLE_CHARS] not in found:
                        found.append(text[:EXAMPLE_CHARS])
            yield row

    def problems_off():
        nonlocal problems
        problems = None

    def frames():
        nonlocal problems
        # One frame per stored row group: the store bounds those by rows and by bytes, so
        # wide rows never build a gigabyte-scale frame.
        rows = observed()
        metadata = pq.read_metadata(path)
        for group in range(metadata.num_row_groups):
            batch = list(itertools.islice(rows, metadata.row_group(group).num_rows))
            if not batch:
                continue
            df = store.frame(batch, manifest)
            if dataset.capability_id is not None:
                capability.append(
                    alignment.capability_contract(dataset.capability, df, dataset.intent)
                )
            checked = problems if "decision" in df.columns else None
            problems = []
            yield df, checked

    try:
        report = contract.measure_frames(frames(), names)
    except BaseException:
        keys.abort()
        raise
    keys.close()
    null_rates = store.null_rates(path)
    fingerprint = store.file_sha256(path)
    stats = statistics.result()
    if analytics.decisions:
        stats["analytics"] = {**analytics.report(), "fingerprint": fingerprint}
    return dict(
        state=Cell.State.OK,
        error="",
        rows=store.row_count(path),
        columns=[
            {
                **c,
                "null_rate": null_rates.get(c["name"], 0.0),
                "examples": examples.get(c["name"], []),
            }
            for c in manifest
        ],
        fingerprint=fingerprint,
        intent_report={"train": report["train"], "eval": report["eval"]},
        capability_report=_merged(capability),
        stats=stats,
        updated_at=timezone.now(),
    )


def _problem(decision) -> str | None:
    """Why ``decision`` is not a valid decision line, or None when it is."""
    if not isinstance(decision, dict):
        return "decision must be an object"
    try:
        decision_line({"decision": decision})
    except (ValueError, TypeError, AttributeError) as exc:
        return str(exc)
    return None


def _merged(parts: list[dict]) -> dict:
    report = {}
    for part in parts:
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


def frame(
    dataset: Dataset,
    cell: Cell,
    path: Path,
    *,
    inherited: Iterable[lineage.Keys] | None = None,
    **fields,
) -> Cell:
    Cell.objects.filter(pk=cell.pk).update(
        **{**describe_frame(dataset, path, inherited=inherited), **fields}
    )
    cell.refresh_from_db()
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
            _merged(
                [
                    alignment.capability_contract(dataset.capability, df, dataset.intent)
                    for df in store.iter_frames(path)
                ]
            )
            if dataset.capability_id is not None
            else {}
        )
        Cell.objects.filter(pk=cell.pk).update(capability_report=report)
