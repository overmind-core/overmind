"""Compose the upstream readers into the committed artifact.

Percentiles rank each score inside its benchmark's full cohort — every model the upstream
leaderboard publishes, not only ours — so trimming the artifact to our catalog afterwards
leaves the ranking unchanged.

Every gate here raises rather than writes: a silently degraded artifact serves wrong
recommendations, while a red sync leaves the committed one in place.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from overbae.modal import model_registry

from .. import artifact as artifact_module
from ..grading import percentile_of
from ..schema import PARSER_VERSION, BenchmarkArtifact, Provenance, parse_artifact
from . import huggingface, leaderboard
from .huggingface import BenchmarkRow, SyncReport
from .leaderboard import EvalScore

MIN_MODELS = 25
MIN_BENCHMARKS = 15
MAX_BENCHMARK_LOSS = 0.5

HUB_ONLY = "hub_only"

# Orgs that re-upload another lab's weights unchanged. The org half of their ids carries no
# signal, so only the model name can join them to the original.
MIRROR_ORGS = frozenset({"unsloth"})

_INSTRUCT_SUFFIX = re.compile(r"-(instruct|it)$")
_PRECISION_SUFFIX = re.compile(r"-(bf16|fp16|f16|fp8|int8|int4|gguf)$")
_PROVENANCE_RANK = {Provenance.MEASURED: 0, Provenance.LAB_CLAIMED: 1}
_MATCH_TIERS = ("exact", "normalized", "name_only")


class SyncGateError(RuntimeError):
    """A gate refused the run: the committed artifact keeps serving until a human looks."""


class TooFewModelsError(SyncGateError): ...


class TooFewBenchmarksError(SyncGateError): ...


class UnknownModelKeyError(SyncGateError): ...


class CoverageLossError(SyncGateError): ...


class ScoreValueError(SyncGateError): ...


class CohortScaleError(SyncGateError): ...


@dataclass(frozen=True, slots=True)
class Match:
    model_key: str
    hf_model_ids: tuple[str, ...]
    hf_model_id: str
    match_type: str
    upstream_slugs: tuple[str, ...]


@dataclass(slots=True)
class BuildResult:
    payload: dict[str, Any]
    matches: list[Match] = field(default_factory=list)
    covered: list[str] = field(default_factory=list)
    uncovered: list[str] = field(default_factory=list)
    benchmarks: list[str] = field(default_factory=list)
    overlaps: list[tuple[str, str]] = field(default_factory=list)
    untagged_dropped: dict[str, int] = field(default_factory=dict)
    hf: SyncReport = field(default_factory=SyncReport)

    @property
    def mean_benchmarks(self) -> float:
        scores = self.payload["models"]
        return sum(len(rows) for rows in scores.values()) / len(scores) if scores else 0.0


def build(
    *,
    upstream_scores: Sequence[EvalScore] | None = None,
    hf_report: SyncReport | None = None,
    generated_at: str | None = None,
) -> BuildResult:
    """Fetch, join, rank and validate. Raises ``SyncGateError`` instead of degrading."""
    day = generated_at or datetime.now(UTC).date().isoformat()
    upstream = list(upstream_scores) if upstream_scores is not None else leaderboard.collect()
    tagged, untagged = _split_untagged(upstream)

    catalog = _catalog()
    indexes = _slug_indexes(tagged)
    known_slugs = {score.model_slug for score in tagged}
    slugs_by_key = _leaderboard_slugs()
    matches = [
        _resolve(
            key,
            hf_ids,
            indexes,
            known_slugs=known_slugs,
            leaderboard_slugs=slugs_by_key.get(key, ()),
        )
        for key, hf_ids in catalog.items()
    ]
    hf = hf_report if hf_report is not None else huggingface.collect(_hf_ids(catalog))

    upstream_by_slug = _group(tagged, lambda score: score.model_slug)
    hf_by_model = _group(hf.rows, lambda row: row["hf_model_id"])

    chosen: dict[str, dict[str, EvalScore | BenchmarkRow]] = {}
    overlaps: list[tuple[str, str]] = []
    for match in matches:
        upstream_rows = _pick(
            (score for slug in match.upstream_slugs for score in upstream_by_slug.get(slug, ())),
            key=lambda score: score.eval_slug,
            rank=lambda score: (_PROVENANCE_RANK[Provenance(score.provenance)], score.raw_score),
        )
        hf_rows = _pick(
            (row for hf_id in match.hf_model_ids for row in hf_by_model.get(hf_id, ())),
            key=lambda row: row["eval_slug"],
            rank=lambda row: (_PROVENANCE_RANK[Provenance(row["provenance"])], row["raw_score"]),
        )
        # The leaderboard runs its own evals; the Hub only carries what a model card reports.
        overlaps.extend(
            (match.model_key, slug) for slug in sorted(hf_rows.keys() & upstream_rows.keys())
        )
        merged: dict[str, EvalScore | BenchmarkRow] = {**hf_rows, **upstream_rows}
        if merged:
            chosen[match.model_key] = merged

    cohorts = _cohorts(tagged, chosen)
    models = {
        model_key: sorted(
            (_emit(row, cohorts, day) for row in rows.values()),
            key=lambda score: score["benchmark"],
        )
        for model_key, rows in sorted(chosen.items())
    }
    by_key = {match.model_key: match for match in matches}
    payload = {
        "generated_at": day,
        "parser_version": PARSER_VERSION,
        "models": models,
        "catalog": {
            model_key: {
                "hf_model_id": by_key[model_key].hf_model_id,
                "match_type": by_key[model_key].match_type,
            }
            for model_key in models
        },
    }

    result = BuildResult(
        payload=payload,
        matches=matches,
        covered=sorted(models),
        uncovered=sorted(set(catalog) - set(models)),
        benchmarks=sorted({score["benchmark"] for rows in models.values() for score in rows}),
        overlaps=overlaps,
        untagged_dropped=untagged,
        hf=hf,
    )
    check(payload, previous=artifact_module.load(refresh=True))
    return result


def check(payload: Mapping[str, Any], *, previous: BenchmarkArtifact) -> None:
    models: Mapping[str, list[dict[str, Any]]] = payload["models"]
    _check_values(models)
    _check_model_keys(models)
    _check_size(models)
    _check_coverage(models, previous)
    parse_artifact(payload)


def write(payload: Mapping[str, Any], path: Path) -> None:
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _check_values(models: Mapping[str, list[dict[str, Any]]]) -> None:
    legal = {p.value for p in Provenance}
    for model_key, scores in models.items():
        for score in scores:
            if score["provenance"] not in legal:
                raise ScoreValueError(
                    f"{model_key} / {score['benchmark']}: provenance {score['provenance']!r}"
                )
            if not 0.0 <= score["percentile"] <= 100.0:
                raise ScoreValueError(
                    f"{model_key} / {score['benchmark']}: percentile {score['percentile']}"
                )


def _check_model_keys(models: Mapping[str, list[dict[str, Any]]]) -> None:
    known = set(_catalog())
    unknown = sorted(set(models) - known)
    if unknown:
        raise UnknownModelKeyError(f"not in models.json: {', '.join(unknown)}")


def _check_size(models: Mapping[str, list[dict[str, Any]]]) -> None:
    if len(models) < MIN_MODELS:
        raise TooFewModelsError(
            f"{len(models)} models carry scores, below the floor of {MIN_MODELS}"
        )
    benchmarks = {score["benchmark"] for scores in models.values() for score in scores}
    if len(benchmarks) < MIN_BENCHMARKS:
        raise TooFewBenchmarksError(
            f"{len(benchmarks)} distinct benchmarks, below the floor of {MIN_BENCHMARKS}"
        )


def _check_coverage(
    models: Mapping[str, list[dict[str, Any]]], previous: BenchmarkArtifact
) -> None:
    for model_key, before in previous.models.items():
        was = len({score.benchmark for score in before})
        now = len({score["benchmark"] for score in models.get(model_key, ())})
        if was and now < was * (1.0 - MAX_BENCHMARK_LOSS):
            raise CoverageLossError(f"{model_key}: {was} benchmarks fell to {now}")


def _catalog() -> dict[str, tuple[str, ...]]:
    """Every catalog id and the HuggingFace ids its per-backend entries point at."""
    by_key: dict[str, list[str]] = {}
    for entry in model_registry.all_model_entries():
        model_key = str(entry.get("id") or "")
        if not model_key:
            continue
        hf_ids = by_key.setdefault(model_key, [])
        hf_id = str(entry.get("hf_model_id") or "")
        if hf_id and hf_id not in hf_ids:
            hf_ids.append(hf_id)
        extras = entry.get("benchmark_hf_model_ids") or []
        if isinstance(extras, list):
            for extra in extras:
                extra_id = str(extra or "")
                if extra_id and extra_id not in hf_ids:
                    hf_ids.append(extra_id)
    return {model_key: tuple(hf_ids) for model_key, hf_ids in sorted(by_key.items())}


def _leaderboard_slugs() -> dict[str, tuple[str, ...]]:
    """Explicit Artificial Analysis slugs from models.json, for records with no Hub URL."""
    by_key: dict[str, tuple[str, ...]] = {}
    for entry in model_registry.all_model_entries():
        model_key = str(entry.get("id") or "")
        raw = entry.get("leaderboard_slugs") or []
        if not model_key or not isinstance(raw, list):
            continue
        slugs = tuple(str(s) for s in raw if str(s).strip())
        if slugs:
            by_key[model_key] = slugs
    return by_key


def _hf_ids(catalog: Mapping[str, tuple[str, ...]]) -> list[str]:
    return sorted({hf_id for hf_ids in catalog.values() for hf_id in hf_ids})


def _split_untagged(scores: Iterable[EvalScore]) -> tuple[list[EvalScore], dict[str, int]]:
    """Drop rows filed under no skill and no domain: nothing can grade them."""
    tagged: list[EvalScore] = []
    dropped: dict[str, int] = {}
    for score in scores:
        if score.skills or score.domains:
            tagged.append(score)
        else:
            dropped[score.eval_slug] = dropped.get(score.eval_slug, 0) + 1
    return tagged, dropped


def _slug_indexes(scores: Sequence[EvalScore]) -> tuple[dict[str, set[str]], ...]:
    indexes: tuple[dict[str, set[str]], ...] = (
        defaultdict(set),
        defaultdict(set),
        defaultdict(set),
    )
    for score in scores:
        for index, key_of in zip(indexes, _KEY_FUNCS, strict=True):
            if key := key_of(score.hf_model_id):
                index[key].add(score.model_slug)
    return indexes


def _resolve(
    model_key: str,
    hf_ids: Sequence[str],
    indexes: Sequence[Mapping[str, set[str]]],
    *,
    known_slugs: set[str] | frozenset[str] = frozenset(),
    leaderboard_slugs: Sequence[str] = (),
) -> Match:
    """Exact id first, then the normalised id, then the bare model name for mirror re-uploads."""
    ids = tuple(hf_ids)
    for match_type, index, key_of in zip(_MATCH_TIERS, indexes, _KEY_FUNCS, strict=True):
        for hf_id in ids:
            slugs = index.get(key_of(hf_id) or "")
            if slugs:
                return Match(model_key, ids, hf_id, match_type, tuple(sorted(slugs)))
    hits = tuple(sorted({slug for slug in leaderboard_slugs if slug in known_slugs}))
    if hits:
        return Match(model_key, ids, ids[0] if ids else "", "slug", hits)
    return Match(model_key, ids, ids[0] if ids else "", HUB_ONLY, ())


def _exact_key(hf_id: str | None) -> str | None:
    return hf_id.lower() if hf_id else None


def _norm_key(hf_id: str | None) -> str | None:
    if not hf_id or "/" not in hf_id:
        return None
    org, name = hf_id.lower().split("/", 1)
    name = _INSTRUCT_SUFFIX.sub("", name)
    if org not in MIRROR_ORGS:
        return f"{org}/{name}"
    # A re-upload names the precision it was converted to; the original weights never do.
    return _PRECISION_SUFFIX.sub("", name)


def _bare_key(hf_id: str | None) -> str | None:
    key = _norm_key(hf_id)
    return key.rsplit("/", 1)[-1] if key else None


_KEY_FUNCS = (_exact_key, _norm_key, _bare_key)


def _group(rows: Iterable[Any], key: Any) -> dict[str, list[Any]]:
    grouped: dict[str, list[Any]] = defaultdict(list)
    for row in rows:
        grouped[key(row)].append(row)
    return grouped


def _pick(rows: Iterable[Any], *, key: Any, rank: Any) -> dict[str, Any]:
    """One row per benchmark across a model's variants: measured beats lab-claimed, and a tie
    keeps the lower score — the weaker, non-thinking configuration is what a fine-tune inherits.
    """
    return {slug: min(group, key=rank) for slug, group in _group(rows, key).items()}


def _cohorts(
    upstream_scores: Sequence[EvalScore], chosen: Mapping[str, Mapping[str, Any]]
) -> dict[tuple[str, str], list[float]]:
    """Ascending scores per benchmark and provenance tier, over every model that carries one.

    Measured and lab-claimed rank separately: a lab's own number against a cohort of
    independent runs would read as a standing it never earned.
    """
    pool: dict[tuple[str, str], list[float]] = defaultdict(list)
    scales: dict[tuple[str, str], set[str]] = defaultdict(set)
    members: list[tuple[str, str, float, str]] = [
        (score.eval_slug, score.provenance, score.raw_score, score.scale)
        for score in upstream_scores
    ]
    # HuggingFace only covers our own models, so its rows join the cohort where they are the
    # emitted value; where the leaderboard won, the model is already a cohort member.
    members.extend(
        (row["eval_slug"], row["provenance"], row["raw_score"], row["scale"])
        for rows in chosen.values()
        for row in rows.values()
        if isinstance(row, dict)
    )
    for benchmark, provenance, raw_score, scale in members:
        pool[benchmark, provenance].append(raw_score)
        scales[benchmark, provenance].add(scale)
    for cohort_key, seen in scales.items():
        if len(seen) > 1:
            raise CohortScaleError(f"{cohort_key[0]}: mixed scales {sorted(seen)} in one cohort")
    return {cohort_key: sorted(values) for cohort_key, values in pool.items()}


def _emit(
    row: EvalScore | BenchmarkRow, cohorts: Mapping[tuple[str, str], list[float]], day: str
) -> dict[str, Any]:
    fields = _hf_fields(row, day) if isinstance(row, dict) else _upstream_fields(row, day)
    cohort = cohorts[fields["benchmark"], fields["provenance"]]
    return {
        **fields,
        "percentile": round(percentile_of(cohort, fields["raw_score"]), 2),
        "cohort_n": len(cohort),
    }


def _upstream_fields(score: EvalScore, day: str) -> dict[str, Any]:
    return {
        "benchmark": score.eval_slug,
        "skills": list(score.skills),
        "domains": list(score.domains),
        "raw_score": score.raw_score,
        "scale": score.scale,
        "source": leaderboard.SOURCE,
        "source_url": leaderboard.BY_SLUG[score.eval_slug].reference,
        # The leaderboard carries no per-score date, so the run date is the only honest one.
        "observed_at": day,
        "provenance": score.provenance,
    }


def _hf_fields(row: BenchmarkRow, day: str) -> dict[str, Any]:
    return {
        "benchmark": row["eval_slug"],
        "skills": list(row["skills"]),
        "domains": list(row["domains"]),
        "raw_score": row["raw_score"],
        "scale": row["scale"],
        "source": huggingface.SOURCE,
        "source_url": row["source_url"] or f"https://huggingface.co/{row['hf_model_id']}",
        "observed_at": row["observed_at"] or day,
        "provenance": row["provenance"],
    }
