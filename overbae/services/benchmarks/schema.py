"""Shape of the committed benchmark artifact, and the contract the sync job must satisfy."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

PARSER_VERSION = 2


class Provenance(StrEnum):
    MEASURED = "measured"
    LAB_CLAIMED = "lab_claimed"


_PROVENANCE_VALUES = frozenset(Provenance)

_REQUIRED_SCORE_KEYS = frozenset(
    {
        "benchmark",
        "skills",
        "domains",
        "raw_score",
        "scale",
        "percentile",
        "cohort_n",
        "source",
        "source_url",
        "observed_at",
        "provenance",
    }
)
_REQUIRED_ARTIFACT_KEYS = frozenset({"generated_at", "parser_version", "models"})
_REQUIRED_MATCH_KEYS = frozenset({"hf_model_id", "match_type"})

# How a catalog id reached its upstream scores. ``hub_only`` means no leaderboard entry
# joined and every score came from the HuggingFace read of that exact id. ``slug`` is an
# explicit models.json leaderboard slug for records that carry no Hub weights URL.
MATCH_TYPES = frozenset({"exact", "normalized", "name_only", "slug", "hub_only"})


class BenchmarkSchemaError(ValueError):
    """The artifact breaks the contract the recommender reads it under."""


@dataclass(frozen=True, slots=True)
class BenchmarkScore:
    benchmark: str
    skills: tuple[str, ...]
    domains: tuple[str, ...]
    raw_score: float
    scale: str
    percentile: float
    cohort_n: int
    source: str
    #: The benchmark's own publication — paper, repository or dataset — not the reader's.
    source_url: str
    observed_at: str
    provenance: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BenchmarkScore:
        _require(_REQUIRED_SCORE_KEYS, data, "score")
        return cls(
            benchmark=str(data["benchmark"]),
            skills=tuple(str(s) for s in data["skills"]),
            domains=tuple(str(d) for d in data["domains"]),
            raw_score=float(data["raw_score"]),
            scale=str(data["scale"]),
            percentile=float(data["percentile"]),
            cohort_n=int(data["cohort_n"]),
            source=str(data["source"]),
            source_url=str(data["source_url"]),
            observed_at=str(data["observed_at"]),
            provenance=str(data["provenance"]),
        )

    def validate(self) -> None:
        if self.provenance not in _PROVENANCE_VALUES:
            raise BenchmarkSchemaError(f"{self.benchmark}: unknown provenance {self.provenance!r}")
        if not 0.0 <= self.percentile <= 100.0:
            raise BenchmarkSchemaError(
                f"{self.benchmark}: percentile {self.percentile} outside [0, 100]"
            )
        if self.cohort_n < 1:
            raise BenchmarkSchemaError(f"{self.benchmark}: cohort_n {self.cohort_n} below 1")
        if not self.benchmark or not self.scale:
            raise BenchmarkSchemaError("score needs a non-empty benchmark and scale")
        if not self.source_url:
            raise BenchmarkSchemaError(f"{self.benchmark}: score carries no source_url")
        if not self.skills and not self.domains:
            raise BenchmarkSchemaError(f"{self.benchmark}: score carries no skill or domain")


@dataclass(frozen=True, slots=True)
class ModelMatch:
    """How a catalog id was joined to its upstream HuggingFace id, so a loose join is auditable."""

    hf_model_id: str
    match_type: str

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> ModelMatch:
        _require(_REQUIRED_MATCH_KEYS, data, "catalog match")
        return cls(hf_model_id=str(data["hf_model_id"]), match_type=str(data["match_type"]))

    def validate(self) -> None:
        if self.match_type not in MATCH_TYPES:
            raise BenchmarkSchemaError(f"unknown match_type {self.match_type!r}")
        if not self.hf_model_id:
            raise BenchmarkSchemaError("catalog match needs a non-empty hf_model_id")


@dataclass(frozen=True, slots=True)
class BenchmarkArtifact:
    generated_at: str
    parser_version: int
    models: dict[str, list[BenchmarkScore]] = field(default_factory=dict)
    catalog: dict[str, ModelMatch] = field(default_factory=dict)

    @classmethod
    def empty(cls) -> BenchmarkArtifact:
        return cls(generated_at="", parser_version=PARSER_VERSION)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> BenchmarkArtifact:
        _require(_REQUIRED_ARTIFACT_KEYS, data, "artifact")
        models = data["models"]
        if not isinstance(models, Mapping):
            raise BenchmarkSchemaError("artifact 'models' must be an object keyed by model id")
        catalog = data.get("catalog") or {}
        if not isinstance(catalog, Mapping):
            raise BenchmarkSchemaError("artifact 'catalog' must be an object keyed by model id")
        return cls(
            generated_at=str(data["generated_at"]),
            parser_version=int(data["parser_version"]),
            models={
                str(model_id): [BenchmarkScore.from_dict(s) for s in scores]
                for model_id, scores in models.items()
            },
            catalog={
                str(model_id): ModelMatch.from_dict(match) for model_id, match in catalog.items()
            },
        )

    def validate(self) -> None:
        if self.parser_version < 1:
            raise BenchmarkSchemaError(f"parser_version {self.parser_version} below 1")
        for model_id, scores in self.models.items():
            if not model_id:
                raise BenchmarkSchemaError("artifact holds an empty model id")
            for score in scores:
                score.validate()
        for model_id, match in self.catalog.items():
            if model_id not in self.models:
                raise BenchmarkSchemaError(f"catalog holds {model_id!r}, which carries no scores")
            match.validate()


def parse_artifact(data: Mapping[str, Any]) -> BenchmarkArtifact:
    artifact = BenchmarkArtifact.from_dict(data)
    artifact.validate()
    return artifact


def _require(keys: frozenset[str], data: Mapping[str, Any], label: str) -> None:
    missing = sorted(keys - data.keys())
    if missing:
        raise BenchmarkSchemaError(f"{label} missing required keys: {', '.join(missing)}")
