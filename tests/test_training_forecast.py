from types import SimpleNamespace
from unittest.mock import patch

import pytest
from conftest import frozen_dataset

from overbae.models import Project
from overbae.services.recommendation import estimate_for_hyperparams
from overbae.services.training_forecast import forecast


def test_native_forecast_uses_matching_measurements_and_selected_gpu():
    recipe = {"objective": "decision_cross_entropy", "context_length": 2048, "batch_size": 128}
    job = SimpleNamespace(
        id="pilot",
        effective_configuration={"gpu_type": "H200", "gpu_count": 1},
        hyperparameters=recipe,
        progress={"tokens_per_second": 4000},
        result={},
        provider="modal",
        cell=SimpleNamespace(stats={"p95_token_length": 1000}),
    )
    with (
        patch("overbae.services.training_forecast.candidates", return_value=[job]),
        patch("overbae.services.training_forecast.hardware", return_value=("H200", 1)),
        patch("overbae.services.training_forecast.gpu_usd_per_second", return_value=0.0015),
    ):
        result = forecast(
            "project", "model", recipe, tokens=400000, stats={"p95_token_length": 1000}
        )
    assert result["basis"] == "matched_measurements"
    assert result["training_seconds"][0] < 100 < result["training_seconds"][1]
    assert result["gpu_type"] == "H200"
    assert result["evidence_jobs"] == ["pilot"]
    assert result["all_in_usd"] is None
    assert result["budget_enforcement"] == "none"


def test_other_objective_or_batch_does_not_calibrate_native_forecast():
    recipe = {"objective": "decision_cross_entropy", "context_length": 2048, "batch_size": 128}
    wrong = SimpleNamespace(
        id="chat",
        hyperparameters={**recipe, "objective": "causal_lm"},
        progress={"tokens_per_second": 90000},
        result={},
        provider="modal",
        cell=SimpleNamespace(stats={"p95_token_length": 1000}),
    )
    with (
        patch("overbae.services.training_forecast.candidates", return_value=[wrong]),
        patch("overbae.services.training_forecast.hardware", return_value=("H200", 1)),
    ):
        result = forecast(
            "project", "model", recipe, tokens=400000, stats={"p95_token_length": 1000}
        )
    assert result["basis"] == "unmeasured_recipe"
    assert result["training_seconds"] is None
    assert result["training_gpu_usd"] is None


@pytest.mark.django_db
@pytest.mark.parametrize("native", [False, True])
def test_estimate_uses_the_selected_split_and_external_validation(native, settings):
    settings.FINETUNING_BACKEND = "modal"
    project = Project.objects.create(name="Quote", slug="quote")
    source = [
        {
            "decision": {
                "state": str(i),
                "question": "Choose",
                "kind": "choice",
                "options": ["a", "b"],
                "target_probabilities": [1, 0],
            }
        }
        if native
        else {
            "messages": [
                {"role": "user", "content": str(i)},
                {"role": "assistant", "content": "answer"},
            ]
        }
        for i in range(20)
    ]
    dataset = frozen_dataset(project, source, contract="train")
    common = {
        "base_model": "Qwen/Qwen3-8B",
        "n_epochs": 1,
        "use_lora": True,
        "cell": dataset.active_cell,
    }
    with patch(
        "overbae.services.training_forecast.forecast",
        return_value={"training_seconds": None, "training_gpu_usd": None},
    ):
        whole = estimate_for_hyperparams(str(dataset.id), **common, validation_enabled=False)
        split = estimate_for_hyperparams(
            str(dataset.id), **common, validation_enabled=True, validation_split_ratio=0.25
        )
        separate = estimate_for_hyperparams(
            str(dataset.id), **common, validation_enabled=True, validation_cell=dataset.active_cell
        )
    assert 0 < split["trained_tokens"] < whole["trained_tokens"]
    assert separate["trained_tokens"] == whole["trained_tokens"]


@pytest.mark.parametrize(
    "effective", [{}, {"gpu_type": "H100", "gpu_count": 1}, {"gpu_type": "H200", "gpu_count": 2}]
)
def test_forecast_does_not_reinterpret_old_hardware_with_todays_planner(effective):
    recipe = {"objective": "decision_cross_entropy", "context_length": 2048, "batch_size": 128}
    job = SimpleNamespace(
        id="older-pilot",
        hyperparameters=recipe,
        effective_configuration=effective,
        progress={"tokens_per_second": 4000},
        cell=SimpleNamespace(stats={"p95_token_length": 1000}),
    )
    with (
        patch("overbae.services.training_forecast.candidates", return_value=[job]),
        patch("overbae.services.training_forecast.hardware", return_value=("H200", 1)),
    ):
        result = forecast(
            "project", "model", recipe, tokens=400000, stats={"p95_token_length": 1000}
        )
    assert result["basis"] == "unmeasured_recipe" and result["training_seconds"] is None
