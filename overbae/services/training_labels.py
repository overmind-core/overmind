import logging

import duckdb
from django.core.cache import cache

from modal_shared.decisions import DECISION_OBJECTIVES, decision_request
from overbae.services.datasets import rows, store
from overbae.services.training_contract import dataset_objective

logger = logging.getLogger(__name__)
LABEL_SET_LIMIT = 8


def prediction_labels(job):
    objective = (job.hyperparameters or {}).get("objective") or dataset_objective(job.cell)
    if objective not in DECISION_OBJECTIVES:
        return None
    cell = job.cell
    unavailable = {"state": "unavailable", "label_sets": [], "total_sets": None}
    if cell is None:
        return {**unavailable, "reason": "No pinned training version."}
    identity = {"cell": str(cell.pk), "fingerprint": cell.fingerprint}
    selection = (job.requested_configuration or {}).get("selection") or {}
    if selection.get("train_cell") not in (None, str(cell.pk)) or selection.get(
        "train_fingerprint"
    ) not in (None, cell.fingerprint):
        return {
            **identity,
            **unavailable,
            "reason": "The training version no longer matches the launch receipt.",
        }
    try:
        path = rows.frame_path(cell)
        stat = path.stat()
        key = f"training-labels:1:{cell.pk}:{cell.fingerprint}:{stat.st_mtime_ns}:{stat.st_size}"
        cached = cache.get(key)
        if cached is not None:
            return cached
        rows.verify(cell)
        # Query every row, but bound the returned label sets and bytes, not the labels themselves.
        result = store.query(
            """
            WITH label_sets AS (
                SELECT DISTINCT
                    json_extract_string(decision, '$.question') AS question,
                    json_extract_string(decision, '$.kind') AS kind,
                    json_extract(decision, '$.options') AS options,
                    json_extract(decision, '$.option_values') AS option_values
                FROM t
            )
            SELECT *, count(*) OVER () AS total_sets FROM label_sets
            ORDER BY question, kind, CAST(options AS VARCHAR), CAST(option_values AS VARCHAR)
            """,
            limit=LABEL_SET_LIMIT,
            max_bytes=12_000,
            t=path,
        )["rows"]
        for group in result:
            decision_request(
                {"state": "", **{key: group[key] for key in ("question", "kind", "options")}}
            )
        summary = {
            **identity,
            "state": "available",
            "total_sets": result[0]["total_sets"] if result else 0,
            "label_sets": [
                {key: value for key, value in group.items() if key != "total_sets"}
                for group in result
            ],
        }
        cache.set(key, summary, timeout=3600)
        return summary
    except (OSError, ValueError, rows.RowStoreError, duckdb.Error):
        logger.warning("Could not read training labels for cell %s", cell.pk, exc_info=True)
        return {**identity, **unavailable, "reason": "Prediction labels could not be read."}
