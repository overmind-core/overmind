from __future__ import annotations

from collections.abc import Iterable

from overbae.services.datasets.partition import split_positions


def _get(row, key, default=None):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def split_datapoint_ids(
    datapoints: Iterable, val_ratio: float, *, method="random", group_by=(), stratify_by=None
):
    """``datapoints`` in order; read once."""
    if not 0 < val_ratio < 1 or method not in {"ordered", "random"}:
        raise ValueError(
            "Choose a validation ratio between zero and one and a random or ordered split."
        )
    ids: list = []

    def records():
        for row in datapoints:
            ids.append(_get(row, "id"))
            yield {
                **(_get(row, "extra", {}) or {}),
                "source_row": ids[-1],
                "input": _get(row, "input", str(ids[-1])),
                "expected_output": _get(row, "expected_output"),
                "source_trace_id": _get(row, "source_trace_id", ""),
            }

    try:
        train, validation, report = split_positions(
            records(),
            eval_percent=val_ratio * 100,
            position="tail" if method == "ordered" else "random",
            group_by=group_by,
            stratify_by=stratify_by,
            deduplicate=False,
        )
    except ValueError:
        if len(ids) < 2:
            return ids, [], []
        raise
    warnings = []
    if report["eval_rows"] != report["target_eval_rows"]:
        warnings.append(f"Group boundaries produced {report['eval_rows']} validation rows.")
    return (
        [i for position, i in enumerate(ids) if position in train],
        [i for position, i in enumerate(ids) if position in validation],
        warnings,
    )
