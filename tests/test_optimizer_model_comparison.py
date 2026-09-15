from __future__ import annotations

import uuid

import pytest
from conftest import frozen_dataset
from rest_framework.exceptions import ValidationError

from overbae.api.optimizer import OptimizerCandidateSerializer, OptimizerExperimentSerializer
from overbae.models import (
    Capability,
    DeployedModel,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
)
from overbae.models import optimizer as optimizer_module
from overbae.services.datasets.rows import row as _dataset_row
from overbae.services.optimizer_create import (
    create_optimizer_experiment,
    validate_optimizer_models,
)

pytestmark = pytest.mark.django_db

MODE = OptimizerExperiment.Mode.MODEL_COMPARISON
MODELS = ["openai/gpt-5", "anthropic/claude-sonnet-4"]


def _experiment(*, mode=MODE, model_ids=None, status=None, openrouter_key_source=None):
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    dataset = frozen_dataset(
        project,
        [
            {"input": {"question": "hello"}, "expected_output": "a"},
            {"input": {"question": "world"}, "expected_output": "b"},
        ],
        capability=capability,
    )
    kwargs = {}
    if openrouter_key_source is not None:
        kwargs["openrouter_key_source"] = openrouter_key_source
    experiment = OptimizerExperiment.objects.create(
        project=project,
        capability=capability,
        dataset=dataset,
        cell=dataset.active_cell,
        mode=mode,
        model_ids=model_ids or list(MODELS),
        status=status or OptimizerExperiment.Status.ITERATING,
        command_template="run __CANDIDATE_ID__",
        scores={"baseline": 80.0, "best": 80.0},
        **kwargs,
    )
    return experiment


@pytest.mark.parametrize("model_ids", [None, [], MODELS * 3, [""], [" openai/gpt-5"]])
def test_model_selection_rejects_missing_too_many_or_malformed(model_ids, monkeypatch):
    monkeypatch.setattr("overbae.services.optimizer_create.is_model_available", lambda _: True)
    with pytest.raises(ValidationError) as exc:
        validate_optimizer_models(MODE, model_ids)
    assert "model_ids" in str(exc.value)


def test_model_selection_rejects_duplicates_and_unavailable(monkeypatch):
    monkeypatch.setattr("overbae.services.optimizer_create.is_model_available", lambda _: True)
    with pytest.raises(ValidationError, match="Duplicate"):
        validate_optimizer_models(MODE, [MODELS[0], MODELS[0]])

    monkeypatch.setattr(
        "overbae.services.optimizer_create.is_model_available",
        lambda model: model != "dead/model",
    )
    with pytest.raises(ValidationError, match="dead/model"):
        validate_optimizer_models(MODE, ["dead/model"])


def test_finetuned_alias_is_not_a_valid_reference():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    capability = Capability.objects.create(
        project=project, name="A", slug=f"a-{uuid.uuid4().hex[:8]}"
    )
    deployed = DeployedModel.objects.create(
        project=project,
        model_id=f"ft-ready-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        base_model_id="meta-llama/Meta-Llama-3.1-8B-Instruct-Reference",
    )
    capability.active_model = deployed
    capability.save(update_fields=["active_model"])

    # The alias chases the capability's *active* model — the comparison must pin
    # the deployment itself, so aliases are rejected outright.
    with pytest.raises(ValidationError, match="overmind/"):
        validate_optimizer_models(MODE, [f"overmind/{capability.id}"], project=project)

    assert validate_optimizer_models(MODE, [deployed.model_id], project=project) == [
        deployed.model_id
    ]


def test_finetuned_deployment_id_requires_ready_in_project():
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ready = DeployedModel.objects.create(
        project=project,
        model_id=f"ft-ready-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.READY,
        base_model_id="meta-llama/Meta-Llama-3.1-8B-Instruct-Reference",
    )

    assert validate_optimizer_models(MODE, [ready.model_id], project=project) == [ready.model_id]

    not_ready = DeployedModel.objects.create(
        project=project,
        model_id=f"ft-busy-{uuid.uuid4().hex[:8]}",
        status=DeployedModel.Status.DEPLOYING,
        base_model_id="meta-llama/Meta-Llama-3.1-8B-Instruct-Reference",
    )
    with pytest.raises(ValidationError, match=not_ready.model_id):
        validate_optimizer_models(MODE, [not_ready.model_id], project=project)

    with pytest.raises(ValidationError, match="ft-nobody"):
        validate_optimizer_models(MODE, ["ft-nobody-00000000"], project=project)


def test_finetuned_references_require_platform_source(monkeypatch):
    monkeypatch.setattr("overbae.services.optimizer_create.is_model_available", lambda _: True)
    with pytest.raises(ValidationError, match="Overmind credits"):
        validate_optimizer_models(
            MODE,
            ["ft-3a4860af-qwen3-5-27b"],
            openrouter_key_source=OptimizerExperiment.OpenRouterKeySource.LOCAL,
        )
    assert validate_optimizer_models(
        MODE,
        [MODELS[0]],
        openrouter_key_source=OptimizerExperiment.OpenRouterKeySource.LOCAL,
    ) == [MODELS[0]]


def test_normal_optimizer_rejects_model_selection():
    with pytest.raises(ValidationError, match="cannot select models"):
        validate_optimizer_models(OptimizerExperiment.Mode.OPTIMIZE, [MODELS[0]])


def test_hybrid_model_selection_is_validated(monkeypatch):
    monkeypatch.setattr("overbae.services.optimizer_create.is_model_available", lambda _: True)
    assert validate_optimizer_models(OptimizerExperiment.Mode.HYBRID, MODELS) == MODELS


def test_create_service_forces_one_iteration_per_model(monkeypatch):
    experiment = _experiment(mode=OptimizerExperiment.Mode.OPTIMIZE)
    monkeypatch.setattr("overbae.services.optimizer_create.is_model_available", lambda _: True)
    created = create_optimizer_experiment(
        user=None,
        capability=experiment.capability,
        dataset=experiment.dataset,
        mode=MODE,
        model_ids=MODELS,
        num_iterations=9,
        num_candidates_per_iteration=9,
        max_iterations_without_improvement=9,
        openrouter_key_source=OptimizerExperiment.OpenRouterKeySource.LOCAL,
    )
    assert created.mode == MODE
    assert created.model_ids == MODELS
    assert created.num_iterations == 2
    assert created.num_candidates_per_iteration == 1
    assert created.max_iterations_without_improvement == 0
    assert created.status == OptimizerExperiment.Status.SCHEDULED


def test_read_serializers_expose_comparison_fields():
    experiment = _experiment()
    iteration = OptimizerIteration.objects.create(experiment=experiment, order=1)
    candidate = OptimizerCandidate.objects.create(
        experiment=experiment,
        iteration=iteration,
        candidate_index=0,
        target_model=MODELS[0],
        code_path="diff --git a/x b/x\n",
    )

    experiment_data = OptimizerExperimentSerializer(experiment).data
    candidate_data = OptimizerCandidateSerializer(candidate).data

    assert experiment_data["mode"] == MODE
    assert experiment_data["model_ids"] == MODELS
    assert candidate_data["target_model"] == MODELS[0]
    assert candidate_data["model_name"] == MODELS[0]
    assert candidate_data["code_path"] == "diff --git a/x b/x\n"
    assert str(candidate_data["experiment"]) == str(experiment.id)


def test_failed_and_empty_outputs_are_excluded_from_candidate_score(monkeypatch):
    experiment = _experiment(mode=OptimizerExperiment.Mode.OPTIMIZE)
    datapoint = _dataset_row(experiment.cell, 1)
    iteration = OptimizerIteration.objects.create(experiment=experiment, order=1)
    candidate = OptimizerCandidate.objects.create(
        experiment=experiment,
        iteration=iteration,
        candidate_index=0,
    )
    OptimizerCommand.objects.create(
        experiment=experiment,
        candidate=candidate,
        iteration=iteration,
        datapoint_index=0,
        input={"question": "hello"},
        status=OptimizerCommand.Status.RAN,
        output="valid output",
        result={"output": "valid output"},
    )
    failed = OptimizerCommand.objects.create(
        experiment=experiment,
        candidate=candidate,
        iteration=iteration,
        datapoint_index=1,
        input=datapoint.input,
        status=OptimizerCommand.Status.FAILED,
        error="provider timeout",
    )
    monkeypatch.setattr(optimizer_module, "randint", lambda *_: 80)

    candidate.evaluate()

    candidate.refresh_from_db()
    failed.refresh_from_db()
    assert candidate.score == 80
    assert candidate.scores["coverage"] == {
        "excluded_commands": 1,
        "errors": ["provider timeout"],
        "scored_rows": 1,
        "graded_rows": 2,
        "total_rows": 2,
        "coverage_rate": 0.5,
    }
    assert failed.status == OptimizerCommand.Status.FAILED


def test_model_telemetry_mismatch_is_a_routing_error():
    experiment = _experiment(mode=OptimizerExperiment.Mode.MODEL_COMPARISON)
    iteration = OptimizerIteration.objects.create(experiment=experiment, order=1)
    candidate = OptimizerCandidate.objects.create(
        experiment=experiment,
        iteration=iteration,
        candidate_index=0,
        target_model=MODELS[0],
        status=OptimizerCandidate.Status.RUNNING_COMMANDS,
    )
    command = OptimizerCommand.objects.create(
        experiment=experiment,
        candidate=candidate,
        iteration=iteration,
        datapoint_index=0,
        status=OptimizerCommand.Status.RAN,
    )
    error = command._telemetry_error(
        {
            "output": "answer",
            "trace_id": "trace-1",
            "telemetry": {"provider": "openrouter", "model": MODELS[1]},
        }
    )
    assert "Model routing mismatch" in error
    assert MODELS[1] in error


def test_eval_variant_label_uses_model_card_identity():
    experiment = _experiment(mode=OptimizerExperiment.Mode.MODEL_COMPARISON)
    candidate = OptimizerCandidate(
        experiment=experiment,
        candidate_index=0,
        target_model=MODELS[0],
    )
    label, model_name = experiment._candidate_label(candidate)
    assert label == MODELS[0]
    assert model_name == MODELS[0]

    baseline = OptimizerCandidate(experiment=experiment, candidate_index=0, is_baseline=True)
    baseline_label, baseline_model = experiment._candidate_label(baseline)
    assert baseline_label == "Baseline"
    assert baseline_model == "Incumbent model"


def test_comparison_scores_report_incumbent_winner_and_stop():
    experiment = _experiment()
    iteration = OptimizerIteration.objects.create(
        experiment=experiment,
        order=1,
        name="Model comparison",
        status=OptimizerIteration.Status.EVALUATED,
    )
    for index, (model, score) in enumerate(zip(MODELS, [75.0, 70.0], strict=True)):
        OptimizerCandidate.objects.create(
            experiment=experiment,
            iteration=iteration,
            candidate_index=index,
            target_model=model,
            score=score,
            status=OptimizerCandidate.Status.EVALUATED,
            code_path=f"+model = '{model}'",
        )

    experiment._record_iteration_scores(iteration)
    experiment.generate_winner()
    experiment.save(update_fields=["scores", "state"])

    # ``best`` never regresses below the incumbent (baseline) — 80.0 wins here.
    assert experiment.scores["best"] == 80.0
    assert experiment.scores["by_model"] == {MODELS[0]: 75.0, MODELS[1]: 70.0}
    assert experiment.scores["models"] == experiment.scores["by_model"]
    assert experiment.state["model_comparison"] == {
        "selected_winner": MODELS[0],
        "selected_winner_score": 75.0,
        "incumbent_score": 80.0,
        "overall_winner": "incumbent",
        "incumbent_wins": True,
    }

    experiment.current_iteration = 0
    experiment.num_iterations = 1
    experiment.status = OptimizerExperiment.Status.ITERATING
    experiment.save(update_fields=["current_iteration", "num_iterations", "status"])
    experiment.generate_next()
    experiment.refresh_from_db()
    assert experiment.current_iteration == 0
    assert experiment.status == OptimizerExperiment.Status.EVALUATED_CANDIDATE_OUTPUTS
    assert experiment.iterations.count() == 1


def test_comparison_continues_until_all_models_scored():
    experiment = _experiment()
    experiment.num_iterations = 3
    experiment.stalled_iterations = 5  # stall never stops a pure comparison run
    experiment.save(update_fields=["num_iterations", "stalled_iterations"])

    assert experiment.should_continue_iterating() is True
    experiment.current_iteration = 3
    experiment.save(update_fields=["current_iteration"])
    assert experiment.should_continue_iterating() is False


def test_hybrid_report_surfaces_best_combination_even_when_incumbent_wins():
    experiment = _experiment(mode=OptimizerExperiment.Mode.HYBRID)
    iteration = OptimizerIteration.objects.create(experiment=experiment, order=1)
    OptimizerCandidate.objects.create(
        experiment=experiment,
        iteration=iteration,
        candidate_index=0,
        target_model=MODELS[0],
        code_path="+combined",
        score=75.0,
        status=OptimizerCandidate.Status.EVALUATED,
    )

    experiment.generate_winner()

    assert experiment.state["model_optimization"] == {
        "selected_model": MODELS[0],
        "selected_harness_candidate": 1,
        "selected_score": 75.0,
        "incumbent_score": 80.0,
        "overall_winner": "incumbent",
    }


def test_validate_optimizer_models_stores_canonical_slugs():
    assert validate_optimizer_models(MODE, ["openrouter/openai/gpt-5"]) == ["openai/gpt-5"]
    assert validate_optimizer_models(MODE, ["openai/gpt-5"]) == ["openai/gpt-5"]


def test_model_comparison_winner_breaks_score_tie_on_coverage():
    experiment = _experiment()
    iteration = OptimizerIteration.objects.create(
        experiment=experiment,
        order=1,
        status=OptimizerIteration.Status.EVALUATED,
    )
    full_coverage = {
        "coverage_rate": 1.0,
        "coverage": {"coverage_rate": 1.0, "excluded_commands": 0},
    }
    partial_coverage = {
        "coverage_rate": 0.5,
        "coverage": {"coverage_rate": 0.5, "excluded_commands": 1},
    }
    OptimizerCandidate.objects.create(
        experiment=experiment,
        iteration=iteration,
        candidate_index=0,
        target_model=MODELS[0],
        score=100.0,
        scores=full_coverage,
        status=OptimizerCandidate.Status.EVALUATED,
    )
    OptimizerCandidate.objects.create(
        experiment=experiment,
        iteration=iteration,
        candidate_index=1,
        target_model=MODELS[1],
        score=100.0,
        scores=partial_coverage,
        status=OptimizerCandidate.Status.EVALUATED,
    )

    experiment.generate_winner()

    assert experiment.state["model_comparison"]["selected_winner"] == MODELS[0]


def test_model_comparison_blocks_winner_when_suite_incomplete_and_all_tied():
    experiment = _experiment()
    iteration = OptimizerIteration.objects.create(
        experiment=experiment,
        order=1,
        status=OptimizerIteration.Status.EVALUATED,
    )
    tied_scores = {
        "coverage_rate": 1.0,
        "coverage": {"coverage_rate": 1.0, "excluded_commands": 0},
        "measurement": {"uncovered_card_claims": ["Returned label matches the gold intent"]},
    }
    for index, model in enumerate(MODELS):
        OptimizerCandidate.objects.create(
            experiment=experiment,
            iteration=iteration,
            candidate_index=index,
            target_model=model,
            score=100.0,
            scores=tied_scores,
            status=OptimizerCandidate.Status.EVALUATED,
        )

    experiment.generate_winner()

    comparison = experiment.state["model_comparison"]
    assert comparison["suite_incomplete"] is True
    assert comparison["selected_winner"] == ""
    assert comparison["overall_winner"] == "incumbent"
    assert "winner_note" in comparison


def test_model_id_matching_accepts_bare_observations_but_keeps_provider():
    assert optimizer_module._normalise_model_id("openrouter/qwen/qwen3-8b") == "qwen/qwen3-8b"
    assert optimizer_module._model_ids_match("openai/gpt-5", "gpt-5")
    assert not optimizer_module._model_ids_match("openai/gpt-5", "anthropic/gpt-5")
