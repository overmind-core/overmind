"""Rank the recurring shapes in a trace sample by how much each looks like a capability.

Providers do not say which observations are capabilities, and only some emit an CAPABILITY
type at all, so the boundary has to be chosen. This profile is the evidence for
that choice: deterministic, advisory, and read-only — nothing here changes ingest
until a mapping is saved.

The load-bearing signal is repetition. An observation appearing once per trace
across the sample is a stage of the run; one appearing twenty times in a single
trace is a loop iteration, whatever it is named.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from typing import Any

from overbae.services.connectors.mapping import SourceConventions
from overbae.services.connectors.records import ObservationRecord


def _model_calls_by_ancestor(
    observations: list[ObservationRecord], conventions: SourceConventions
) -> dict[str, int]:
    """How many model calls sit under each observation."""
    by_id = {obs.id: obs for obs in observations}
    counts: dict[str, int] = defaultdict(int)
    for obs in observations:
        if not conventions.is_model_call(obs.type):
            continue
        seen: set[str] = set()
        parent = by_id.get(obs.parent_observation_id or "")
        while parent is not None and parent.id not in seen:
            seen.add(parent.id)
            counts[parent.id] += 1
            parent = by_id.get(parent.parent_observation_id or "")
    return counts


def _score(
    stat: dict[str, Any], sampled: int, conventions: SourceConventions
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    if conventions.is_capability_type(stat["type"]):
        score += 3
        reasons.append(f"declared as an {stat['type']} observation")
    if stat["is_root"]:
        score += 2
        reasons.append("roots the trace")
    if stat["model_calls"]:
        score += 2
        reasons.append(f"owns {stat['model_calls']} model calls")
    if conventions.is_model_call(stat["type"]):
        score -= 1
        reasons.append("is a model call rather than something that makes them")
    if stat["max_per_trace"] > 1:
        score -= 3
        reasons.append(f"repeats {stat['max_per_trace']}x within a trace, so it is a loop step")
    elif sampled and stat["traces"] >= sampled / 2:
        score += 1
        reasons.append(f"appears once per trace in {stat['traces']} of {sampled}")
    return score, reasons


def profile_capability_candidates(
    traces: Iterable[list[ObservationRecord]],
    conventions: SourceConventions,
) -> list[dict[str, Any]]:
    """Rank (name, type) shapes across a trace sample, best candidate first."""
    stats: dict[tuple[str, str], dict[str, Any]] = {}
    parents: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    sampled = 0

    for observations in traces:
        if not observations:
            continue
        sampled += 1
        by_id = {obs.id: obs for obs in observations}
        model_calls = _model_calls_by_ancestor(observations, conventions)
        per_trace = Counter((obs.name or obs.type, obs.type) for obs in observations)

        for signature, count in per_trace.items():
            stat = stats.setdefault(
                signature,
                {
                    "name": signature[0],
                    "type": signature[1],
                    "traces": 0,
                    "occurrences": 0,
                    "max_per_trace": 0,
                    "model_calls": 0,
                    "is_root": False,
                },
            )
            stat["traces"] += 1
            stat["occurrences"] += count
            stat["max_per_trace"] = max(stat["max_per_trace"], count)

        for obs in observations:
            stat = stats[(obs.name or obs.type, obs.type)]
            stat["model_calls"] = max(stat["model_calls"], model_calls.get(obs.id, 0))
            if obs.is_root_observation or obs.parent_observation_id is None:
                stat["is_root"] = True
            parent = by_id.get(obs.parent_observation_id or "")
            if parent is not None:
                parents[(obs.name or obs.type, obs.type)][parent.name or parent.type] += 1

    candidates = []
    for signature, stat in stats.items():
        score, reasons = _score(stat, sampled, conventions)
        common = parents[signature].most_common(1)
        candidates.append(
            {
                **stat,
                "parent_name": common[0][0] if common else None,
                "score": score,
                "reasons": reasons,
            }
        )
    candidates.sort(key=lambda c: (-c["score"], -c["model_calls"], c["name"]))
    return candidates
