"""Per-class precision, recall and F1 for a run whose references are labels."""

from __future__ import annotations

import logging
from typing import Any

from sklearn.metrics import accuracy_score, confusion_matrix, precision_recall_fscore_support

from overbae.models import EvalRun
from overbae.services.datasets.profiler import profile_dataset
from overbae.services.datasets.rows import RowStoreError, iter_rows

logger = logging.getLogger(__name__)


def normalize(label: Any) -> str | None:
    return label.strip().lower() if isinstance(label, str) and label.strip() else None


def is_classification(run: EvalRun) -> bool:
    if not run.dataset_id:
        return False
    profile = profile_dataset(run.dataset)
    return bool(profile.get("has_reference")) and profile.get("output_kind") == "label"


def labelled_pairs(run: EvalRun) -> tuple[list[str], list[str]]:
    references = {row.index: normalize(row.expected_output) for row in iter_rows(run.cell)}
    predicted, expected = [], []
    outputs = run.rows.filter(error="", degraded=False).values_list("row_index", "output")
    for index, output in outputs:
        reference = references.get(index)
        if reference is None:
            continue
        predicted.append(normalize(output) or "")
        expected.append(reference)
    return predicted, expected


def scores(predicted: list[str], expected: list[str]) -> dict[str, Any]:
    labels = sorted(set(expected) | set(predicted))
    precision, recall, f1, support = precision_recall_fscore_support(
        expected, predicted, labels=labels, average=None, zero_division=0
    )
    aggregates: dict[str, Any] = {
        "accuracy": round(float(accuracy_score(expected, predicted)), 6),
        "n": len(expected),
    }
    for average in ("macro", "micro", "weighted"):
        p, r, f, _ = precision_recall_fscore_support(
            expected, predicted, labels=labels, average=average, zero_division=0
        )
        aggregates[average] = {
            "precision": round(float(p), 6),
            "recall": round(float(r), 6),
            "f1": round(float(f), 6),
        }
    return {
        "classes": [
            {
                "label": label,
                "precision": round(float(p), 6),
                "recall": round(float(r), 6),
                "f1": round(float(f), 6),
                "support": int(s),
            }
            for label, p, r, f, s in zip(labels, precision, recall, f1, support, strict=True)
        ],
        "aggregates": aggregates,
        "confusion_matrix": {
            "labels": labels,
            "matrix": confusion_matrix(expected, predicted, labels=labels).tolist(),
        },
    }


def class_metrics(run: EvalRun) -> dict[str, Any] | None:
    """``None`` when the references are not labels: free text would make one class per
    distinct string."""
    if not run.cell_id:
        return None
    try:
        if not is_classification(run):
            return None
        predicted, expected = labelled_pairs(run)
    except (RowStoreError, OSError, ValueError):
        logger.warning("Class metrics unavailable for run %s", run.pk, exc_info=True)
        return None
    return scores(predicted, expected) if expected else None
