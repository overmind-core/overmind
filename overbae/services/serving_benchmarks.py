"""Precomputed dedicated-serving speed. One row per base model, shape, and GPU."""

from __future__ import annotations

import json
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "modal" / "serving_benchmarks.json"
_cached: tuple[int, dict] | None = None


def _catalog() -> dict:
    global _cached
    stamp = _PATH.stat().st_mtime_ns
    if _cached is not None and _cached[0] == stamp:
        return _cached[1]
    data = json.loads(_PATH.read_text())
    _cached = (stamp, data)
    return data


def workload() -> dict:
    return dict(_catalog().get("workload") or {})


def measurement(base_model_id: str, shape: str, gpu_type: str) -> dict | None:
    row = (((_catalog().get("models") or {}).get(base_model_id) or {}).get(shape) or {}).get(
        gpu_type
    )
    return dict(row) if isinstance(row, dict) else None
