from __future__ import annotations

from overbae.services.datasets.partition import split_rows


def _get(row, key, default=None):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def split_datapoint_ids(
    datapoints: list, val_ratio: float, *, method="random", group_by=(), stratify_by=None
):
    if not 0 < val_ratio < 1 or method not in {"ordered", "random"}:
        raise ValueError(
            "Choose a validation ratio between zero and one and a random or ordered split."
        )
    ordered = sorted(datapoints, key=lambda row: (_get(row, "order", 0), str(_get(row, "id"))))
    if len(ordered) < 2:
        return [_get(row, "id") for row in ordered], [], []
    records = []
    for row in ordered:
        records.append(
            {
                **(_get(row, "extra", {}) or {}),
                "source_row": _get(row, "id"),
                "input": _get(row, "input", str(_get(row, "id"))),
                "expected_output": _get(row, "expected_output"),
                "source_trace_id": _get(row, "source_trace_id", ""),
            }
        )
    train, validation, report = split_rows(
        records,
        eval_percent=val_ratio * 100,
        position="tail" if method == "ordered" else "random",
        group_by=group_by,
        stratify_by=stratify_by,
        deduplicate=False,
    )
    warnings = []
    if report["eval_rows"] != report["target_eval_rows"]:
        warnings.append(f"Group boundaries produced {report['eval_rows']} validation rows.")
    return [row["source_row"] for row in train], [row["source_row"] for row in validation], warnings
