"""Trace-safe train/validation splits for finetuning JSONL materialisation.

Callers pass any datapoint-like object (model instance, dict, SimpleNamespace).

``random`` (default) groups on ``source_trace_id`` so a multi-row trace never
straddles train and val; blank trace ids form singleton groups. ``ordered`` is a
plain first-N-by-``order`` holdout and deliberately does not group — intentional
for time-ordered corpora. When ``n >= 2`` both splits receive at least one row.
"""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.model_selection import GroupShuffleSplit, train_test_split

SPLIT_SEED = 42  # deterministic split so re-runs train on the same rows


def split_datapoint_ids(
    datapoints: list,
    val_ratio: float,
    *,
    method: str = "random",
) -> tuple[list, list, list[str]]:
    """Return ``(train_ids, val_ids, warnings)``."""
    warnings: list[str] = []
    if not datapoints:
        return [], [], warnings

    ordered = _sorted_datapoints(datapoints)
    all_ids = [_id(dp) for dp in ordered]
    n = len(all_ids)

    if n == 1:
        return all_ids[:], [], warnings

    if method == "ordered":
        train_ids, val_ids = _ordered_split(ordered, val_ratio)
    else:
        train_ids, val_ids = _random_split(ordered, val_ratio, warnings)

    train_ids, val_ids = _ensure_min_one_each(train_ids, val_ids, all_ids)
    return train_ids, val_ids, warnings


def _get(dp: Any, name: str, default: Any = None) -> Any:
    if hasattr(dp, name):
        return getattr(dp, name, default)
    if isinstance(dp, dict):
        return dp.get(name, default)
    return default


def _id(dp: Any):
    return _get(dp, "id")


def _order(dp: Any) -> int:
    return int(_get(dp, "order", 0) or 0)


def _sorted_datapoints(datapoints: list) -> list:
    return sorted(datapoints, key=lambda dp: (_order(dp), str(_id(dp))))


def _group_key(dp: Any) -> str:
    trace_id = (_get(dp, "source_trace_id", "") or "").strip()
    return trace_id if trace_id else str(_id(dp))


def _ordered_split(datapoints: list, val_ratio: float) -> tuple[list, list]:
    ids = [_id(dp) for dp in datapoints]
    split_idx = max(1, int(len(ids) * (1 - val_ratio)))
    return ids[:split_idx], ids[split_idx:]


def _random_split(
    datapoints: list,
    val_ratio: float,
    warnings: list[str],
) -> tuple[list, list]:
    groups = [_group_key(dp) for dp in datapoints]
    unique_groups = set(groups)

    if len(unique_groups) < 2:
        return _row_level_split(datapoints, val_ratio, warnings=warnings)

    indices = np.arange(len(datapoints))
    gss = GroupShuffleSplit(n_splits=1, test_size=val_ratio, random_state=SPLIT_SEED)
    train_idx, val_idx = next(gss.split(indices, groups=groups))
    return _ids_from_indices(datapoints, train_idx, val_idx)


def _row_level_split(
    datapoints: list,
    val_ratio: float,
    *,
    warnings: list[str],
) -> tuple[list, list]:
    ids = [_id(dp) for dp in datapoints]
    try:
        train_ids, val_ids = train_test_split(
            ids,
            test_size=val_ratio,
            random_state=SPLIT_SEED,
        )
    except ValueError:
        warnings.append("Random split fell back to ordered holdout")
        return _ordered_split(datapoints, val_ratio)
    return list(train_ids), list(val_ids)


def _ids_from_indices(
    datapoints: list, train_idx: np.ndarray, val_idx: np.ndarray
) -> tuple[list, list]:
    all_ids = [_id(dp) for dp in datapoints]
    return [all_ids[i] for i in train_idx], [all_ids[i] for i in val_idx]


def _ensure_min_one_each(train_ids: list, val_ids: list, all_ids: list) -> tuple[list, list]:
    if len(all_ids) < 2:
        return train_ids, val_ids

    train = list(train_ids)
    val = list(val_ids)

    if not val and len(train) > 1:
        val = [train.pop()]
    elif not train and len(val) > 1:
        train = [val.pop()]

    return train, val
