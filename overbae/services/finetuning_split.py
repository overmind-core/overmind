from __future__ import annotations

from overbae.services.datasets.partition import contamination_keys, content_key, split_rows


def _get(row, key, default=None):
    return row.get(key, default) if isinstance(row, dict) else getattr(row, key, default)


def split_datapoint_ids(
    datapoints, val_ratio: float, *, method="random", group_by=(), stratify_by=None
):
    if not 0 < val_ratio < 1 or method not in {"ordered", "random"}:
        raise ValueError(
            "Choose a validation ratio between zero and one and a random or ordered split."
        )
    records = []
    for row in datapoints:
        record = {
            **(_get(row, "extra", {}) or {}),
            "source_row": _get(row, "id"),
            "input": _get(row, "input", str(_get(row, "id"))),
            "expected_output": _get(row, "expected_output"),
            "source_trace_id": _get(row, "source_trace_id", ""),
        }
        keys = contamination_keys(record, group_by)
        records.append(
            {
                "source_row": record["source_row"],
                "order": _get(row, "order", 0),
                "input": content_key(record),
                **{key: record[key] for key in (*group_by, stratify_by) if key in record},
                "_overmind_provenance": {
                    "source_content_keys": sorted(
                        value for kind, value in keys if kind == "content"
                    ),
                    "source_group_keys": sorted(
                        [kind, value] for kind, value in keys if kind != "content"
                    ),
                },
            }
        )
    records.sort(key=lambda row: (row["order"], str(row["source_row"])))
    if len(records) < 2:
        return [row["source_row"] for row in records], [], []
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
