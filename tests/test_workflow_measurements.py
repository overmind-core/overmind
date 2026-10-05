from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from overbae.services import training_release
from overbae.services.compute_costs import estimate_usage
from overbae.services.training_forecast import forecast


def measured_job():
    stats = {
        "num_examples": 1000,
        "avg_input_chars": 1600,
        "avg_output_chars": 0,
        "p95_token_length": 1000,
    }
    recipe = {
        "objective": "decision_cross_entropy",
        "context_length": 2048,
        "batch_size": 128,
        "n_epochs": 1,
    }
    start = datetime(2026, 10, 4, tzinfo=UTC)
    return SimpleNamespace(
        id="completed-pilot",
        requested_configuration={"runtime": training_release.current()},
        effective_configuration={"gpu_type": "H200", "gpu_count": 1},
        hyperparameters=recipe,
        cell=SimpleNamespace(stats=stats),
        progress={"tokens_per_second": 999999, "trained_steps": 100, "total_steps": 100},
        started_at=start,
        completed_at=start + timedelta(seconds=200),
        result={},
    )


def test_forecast_uses_complete_execution_instead_of_last_batch_speed():
    job = measured_job()
    job.requested_configuration["runtime"]["release"] = "telemetry-wrapper-only-change"
    with (
        patch("overbae.services.training_forecast.candidates", return_value=[job]),
        patch("overbae.services.training_forecast.hardware", return_value=("H200", 1)),
    ):
        value = forecast(
            "project", "model", job.hyperparameters, tokens=400000, stats=job.cell.stats
        )
    assert value["training_seconds"][0] <= 200 <= value["training_seconds"][1]
    assert value["confidence"] == "low"
    assert value["evidence"][0]["elapsed_seconds"] == 200
    assert (
        value["measurement_window"]
        == "completed_gpu_execution_including_load_validation_and_reload"
    )


@pytest.mark.parametrize("invalid", ["runtime", "incomplete", "duration", "profile"])
def test_invalid_measurements_explain_why_no_forecast_is_available(invalid):
    job = measured_job()
    stats = dict(job.cell.stats)
    if invalid == "runtime":
        job.requested_configuration["runtime"]["training"] = "other-training-engine"
    elif invalid == "incomplete":
        job.progress["trained_steps"] = 20
    elif invalid == "duration":
        job.completed_at = job.started_at
    else:
        stats["avg_input_chars"] *= 10
    with (
        patch("overbae.services.training_forecast.candidates", return_value=[job]),
        patch("overbae.services.training_forecast.hardware", return_value=("H200", 1)),
    ):
        value = forecast("project", "model", job.hyperparameters, tokens=400000, stats=stats)
    assert value["training_seconds"] is None
    assert value["rejected_measurements"][invalid] == 1


def test_cost_estimates_deduplicate_cumulative_usage_and_never_duplicate_recorded_gpu():
    usage = {
        "usage_id": "worker-1",
        "elapsed_seconds": 100,
        "gpu_type": "H100",
        "gpu_count": 1,
        "cpu_core_seconds": 200,
        "memory_gib_seconds": 400,
    }
    later = {**usage, "elapsed_seconds": 200, "cpu_core_seconds": 400, "memory_gib_seconds": 800}
    value = estimate_usage([usage, later, later])
    assert value["unique_measurements"] == 1
    assert value["components_usd"]["gpu"] == pytest.approx(200 * 3.95 / 3600)
    assert value["components_usd"]["cpu"] == pytest.approx(400 * 0.0473 / 3600)
    assert value["components_usd"]["memory"] == pytest.approx(800 * 0.008 / 3600)
    assert value["all_in_actual_usd"] is None
    additional = estimate_usage([later], recorded_gpu=True)
    assert additional["components_usd"]["gpu"] == 0
    assert additional["gpu_covered_by_ledger"] is True


def test_missing_or_invalid_compute_usage_remains_unknown():
    for usage in (
        {},
        {"usage_id": "bad", "elapsed_seconds": float("nan")},
        {"usage_id": "bad", "elapsed_seconds": -1},
    ):
        assert estimate_usage([usage])["estimated_usd"] is None
    unknown = {
        "usage_id": "unknown-gpu",
        "elapsed_seconds": 2,
        "gpu_type": "future-gpu",
        "gpu_count": 1,
        "cpu_core_seconds": 1,
        "memory_gib_seconds": 1,
    }
    value = estimate_usage([unknown])
    assert value["estimated_usd"] is None
    assert "gpu" in value["unmeasured_components"]
