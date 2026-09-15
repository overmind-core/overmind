"""Reads and caches the committed benchmark artifact.

Never raises: a missing or broken file degrades to an empty artifact so the recommender
still answers, ungraded.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from .schema import BenchmarkArtifact, BenchmarkSchemaError, BenchmarkScore, parse_artifact

logger = logging.getLogger(__name__)

ARTIFACT_PATH = Path(__file__).parent / "data" / "benchmark_results.json"

_cached: BenchmarkArtifact | None = None


def load(*, refresh: bool = False) -> BenchmarkArtifact:
    global _cached
    if _cached is None or refresh:
        _cached = _read()
    return _cached


def scores_for(model_id: str) -> list[BenchmarkScore]:
    return list(load().models.get(model_id, ()))


def _read() -> BenchmarkArtifact:
    try:
        payload = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("Benchmark artifact missing at %s; models will be ungraded", ARTIFACT_PATH)
        return BenchmarkArtifact.empty()
    except (OSError, json.JSONDecodeError):
        logger.warning(
            "Benchmark artifact unreadable at %s; models will be ungraded",
            ARTIFACT_PATH,
            exc_info=True,
        )
        return BenchmarkArtifact.empty()

    try:
        return parse_artifact(payload)
    except BenchmarkSchemaError:
        logger.warning(
            "Benchmark artifact at %s failed validation; models will be ungraded",
            ARTIFACT_PATH,
            exc_info=True,
        )
        return BenchmarkArtifact.empty()
