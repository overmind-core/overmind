from __future__ import annotations

import uuid

import pytest
from conftest import frozen_dataset
from factories import make_span
from rest_framework.exceptions import ValidationError

from overbae.api.optimizer import OptimizerCandidateSerializer, OptimizerExperimentSerializer
from overbae.celery import app as celery_app
from overbae.models import (
    Capability,
    DeployedModel,
    Evaluator,
    OptimizerCandidate,
    OptimizerCommand,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
)
from overbae.services.optimizer_create import (
    create_optimizer_experiment,
    validate_optimizer_models,
)
from overbae.services.optimizer_ledger import (
    complete_experiment,
    create_iteration,
    evaluate_iteration,
    post_results,
)

pytestmark = pytest.mark.django_db

MODE = OptimizerExperiment.Mode.MODEL_COMPARISON
MODELS = ["openai/gpt-5", "anthropic/claude-sonnet-4"]


@pytest.fixture(autouse=True)
def _listed(fake_llm):
    fake_llm.extra_models = list(MODELS)


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
        contract="eval",
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
def test_model_selection_rejects_missing_too_many_or_malformed(model_ids):
    with pytest.raises(ValidationError) as exc:
        validate_optimizer_models(MODE, model_ids)
    assert "model_ids" in str(exc.value)


def test_model_selection_rejects_duplicates_and_unavailable():
    with pytest.raises(ValidationError, match="Duplicate"):
        validate_optimizer_models(MODE, [MODELS[0], MODELS[0]])

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


def test_finetuned_references_require_platform_source():
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


def test_hybrid_model_selection_is_validated():
    assert validate_optimizer_models(OptimizerExperiment.Mode.HYBRID, MODELS) == MODELS


def test_create_service_forces_one_iteration_per_model():
    experiment = _experiment(mode=OptimizerExperiment.Mode.OPTIMIZE)
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


@pytest.fixture
def eager(monkeypatch):
    monkeypatch.setattr(celery_app.conf, "task_always_eager", True)
    monkeypatch.setattr(celery_app.conf, "task_eager_propagates", True)


def _grade(experiment, candidate: dict, results: list[dict], *, order: int = 1):
    Evaluator.objects.create(
        project=experiment.project,
        capability=experiment.capability,
        name="ExactMatch",
        kind="deterministic",
        scope="final_output",
        config={"check": "exact_match"},
        pass_threshold=1.0,
        version=1,
    )
    iteration = create_iteration(experiment, order=order, candidates=[candidate])
    posted = iteration.candidates.get()
    post_results(experiment, [{"candidate_id": str(posted.id), **r} for r in results])
    evaluate_iteration(experiment, order)
    posted.refresh_from_db()
    return posted


def test_failed_and_empty_outputs_are_excluded_from_the_stub_score():
    experiment = _experiment(mode=OptimizerExperiment.Mode.OPTIMIZE)
    iteration = create_iteration(experiment, order=1, candidates=[{"candidate_index": 0}])
    candidate = iteration.candidates.get()
    post_results(
        experiment,
        [
            {"candidate_id": str(candidate.id), "datapoint_index": 0, "output": "valid output"},
            {
                "candidate_id": str(candidate.id),
                "datapoint_index": 1,
                "success": False,
                "error": "provider timeout",
            },
        ],
    )

    evaluate_iteration(experiment, 1)

    candidate.refresh_from_db()
    assert 60 <= candidate.score <= 85
    assert candidate.scores["coverage"] == {
        "excluded_commands": 1,
        "errors": ["provider timeout"],
        "scored_rows": 1,
        "graded_rows": 2,
        "total_rows": 2,
        "coverage_rate": 0.5,
    }
    assert candidate.commands.get(datapoint_index=1).status == OptimizerCommand.Status.FAILED


@pytest.mark.parametrize(
    ("target", "observed", "routed"),
    [
        ("openai/gpt-5", "openai/gpt-5", True),
        ("openai/gpt-5-mini", "gpt-5-mini", True),
        ("qwen/qwen3-14b", "qwen3-14b", True),
        ("openrouter/qwen/qwen3-14b", "qwen3-14b", True),
        ("openai/gpt-5", "openrouter/openai/gpt-5", True),
        ("deepseek/deepseek-v4-flash", "DeepSeek/DeepSeek-V4-Flash", True),
        ("ft-abc", "ft-abc", True),
        ("openai/gpt-5", "anthropic/claude-sonnet-4", False),
        ("openai/gpt-5-mini", "anthropic/gpt-5-mini", False),
        ("openai/gpt-5-mini", "openai/gpt-5.4", False),
        ("ft-abc", "ft-def", False),
    ],
)
def test_a_trace_served_by_another_model_fails_the_datapoint(eager, target, observed, routed):
    experiment = _experiment()
    make_span(
        experiment.project,
        trace_id="a" * 32,
        span_type="llm",
        attributes={"gen_ai.request.model": observed},
    )

    candidate = _grade(
        experiment,
        {"candidate_index": 0, "target_model": target},
        [
            {"datapoint_index": 0, "output": "a", "trace_id": "a" * 32},
            {"datapoint_index": 1, "output": "b"},
        ],
    )

    command = candidate.commands.get(datapoint_index=0)
    assert candidate.scores["coverage"]["excluded_commands"] == (0 if routed else 1)
    if not routed:
        assert command.error.startswith("Model routing mismatch")
        assert observed in command.error


@pytest.mark.parametrize(
    ("mode", "candidate", "label", "model_name"),
    [
        (MODE, {"target_model": MODELS[0]}, MODELS[0], MODELS[0]),
        (MODE, {"is_baseline": True}, "Baseline", "Incumbent model"),
        (
            OptimizerExperiment.Mode.HYBRID,
            {"candidate_index": 2, "target_model": MODELS[1]},
            f"Candidate 2 · {MODELS[1]}",
            MODELS[1],
        ),
        (
            OptimizerExperiment.Mode.OPTIMIZE,
            {"candidate_index": 1},
            "Candidate 2",
            "Incumbent model",
        ),
    ],
    ids=["comparison", "baseline", "hybrid", "optimize"],
)
def test_the_eval_variant_carries_the_candidate_identity(eager, mode, candidate, label, model_name):
    experiment = _experiment(mode=mode)
    graded = _grade(experiment, candidate, [{"datapoint_index": 0, "output": "a"}])

    variant = graded.eval_run.variants.get()
    assert (variant.label, variant.model_name) == (label, model_name)


def test_comparison_scores_report_the_incumbent_when_it_wins():
    experiment = _experiment()
    iteration = OptimizerIteration.objects.create(experiment=experiment, order=1)
    for index, (model, score) in enumerate(zip(MODELS, [75.0, 70.0], strict=True)):
        OptimizerCandidate.objects.create(
            experiment=experiment,
            iteration=iteration,
            candidate_index=index,
            target_model=model,
            score=score,
            code_path=f"+model = '{model}'",
        )

    experiment.on_iteration_eval_complete(1)
    complete_experiment(experiment)

    experiment.refresh_from_db()
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

    complete_experiment(experiment)

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

    complete_experiment(experiment)

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

    complete_experiment(experiment)

    comparison = experiment.state["model_comparison"]
    assert comparison["suite_incomplete"] is True
    assert comparison["selected_winner"] == ""
    assert comparison["overall_winner"] == "incumbent"
    assert "winner_note" in comparison
