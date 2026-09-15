from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from overbae.services.benchmarks import artifact
from overbae.services.benchmarks.schema import (
    PARSER_VERSION,
    BenchmarkSchemaError,
    Provenance,
    parse_artifact,
)

_LEGAL_PROVENANCE = {p.value for p in Provenance}


@pytest.fixture(autouse=True)
def _isolate_artifact_cache(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(artifact, "_cached", None)


def _payload(**score_overrides: Any) -> dict[str, Any]:
    score: dict[str, Any] = {
        "benchmark": "ifbench",
        "skills": ["Instruction Following"],
        "domains": [],
        "raw_score": 0.651,
        "scale": "accuracy_0_1",
        "percentile": 88.2,
        "cohort_n": 450,
        "source": "leaderboard",
        "source_url": "https://arxiv.org/abs/2507.02833",
        "observed_at": "2026-08-11",
        "provenance": "measured",
    }
    score.update(score_overrides)
    return {
        "generated_at": "2026-08-11",
        "parser_version": PARSER_VERSION,
        "models": {"Qwen/Qwen3.5-9B": [score]},
    }


def test_valid_payload_parses():
    parsed = parse_artifact(_payload())

    assert parsed.parser_version == PARSER_VERSION
    assert parsed.models["Qwen/Qwen3.5-9B"][0].percentile == pytest.approx(88.2)


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"provenance": "vibes"}, "unknown provenance"),
        ({"percentile": 100.1}, "outside"),
        ({"percentile": -0.1}, "outside"),
        ({"cohort_n": 0}, "below 1"),
        ({"skills": [], "domains": []}, "no skill or domain"),
    ],
)
def test_validate_rejects_broken_scores(override: dict[str, Any], message: str):
    with pytest.raises(BenchmarkSchemaError, match=message):
        parse_artifact(_payload(**override))


def test_validate_rejects_a_score_missing_required_keys():
    payload = _payload()
    del payload["models"]["Qwen/Qwen3.5-9B"][0]["cohort_n"]

    with pytest.raises(BenchmarkSchemaError, match="cohort_n"):
        parse_artifact(payload)


@pytest.mark.parametrize("percentile", [0.0, 100.0])
def test_validate_accepts_the_percentile_ends(percentile: float):
    parse_artifact(_payload(percentile=percentile))


def test_missing_file_loads_an_empty_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(artifact, "ARTIFACT_PATH", tmp_path / "absent.json")

    loaded = artifact.load(refresh=True)

    assert loaded.models == {}
    assert loaded.generated_at == ""
    assert artifact.scores_for("Qwen/Qwen3.5-9B") == []


def test_unparseable_file_loads_an_empty_artifact(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    broken = tmp_path / "benchmark_results.json"
    broken.write_text("{ not json", encoding="utf-8")
    monkeypatch.setattr(artifact, "ARTIFACT_PATH", broken)

    assert artifact.load(refresh=True).models == {}


def test_file_breaking_the_schema_loads_an_empty_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    degraded = tmp_path / "benchmark_results.json"
    degraded.write_text(json.dumps(_payload(provenance="vibes")), encoding="utf-8")
    monkeypatch.setattr(artifact, "ARTIFACT_PATH", degraded)

    assert artifact.load(refresh=True).models == {}


def test_load_caches_until_refreshed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "benchmark_results.json"
    path.write_text(json.dumps(_payload()), encoding="utf-8")
    monkeypatch.setattr(artifact, "ARTIFACT_PATH", path)

    first = artifact.load(refresh=True)
    assert artifact.load() is first

    path.write_text(json.dumps({**_payload(), "models": {}}), encoding="utf-8")
    assert artifact.load().models
    assert artifact.load(refresh=True).models == {}


def test_committed_artifact_parses_and_validates():
    payload = json.loads(artifact.ARTIFACT_PATH.read_text(encoding="utf-8"))

    parsed = parse_artifact(payload)

    assert parsed.parser_version == PARSER_VERSION
    assert parsed.generated_at
    for model_id, scores in parsed.models.items():
        assert model_id
        for score in scores:
            assert score.provenance in _LEGAL_PROVENANCE
            assert 0.0 <= score.percentile <= 100.0


def test_committed_artifact_is_what_load_serves():
    loaded = artifact.load(refresh=True)

    assert loaded.parser_version == PARSER_VERSION
    assert loaded.generated_at
