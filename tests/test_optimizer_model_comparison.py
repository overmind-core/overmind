from __future__ import annotations

import uuid

import pytest
from conftest import frozen_dataset
from factories import make_matrix_row
from rest_framework.exceptions import ValidationError

from overbae.models import (
    Capability,
    DeployedModel,
    EvalMatrix,
    OptimizerCandidate,
    OptimizerExperiment,
    OptimizerIteration,
    Project,
)
from overbae.services.optimizer_create import (
    validate_optimizer_models,
)
from overbae.services.optimizer_ledger import (
    complete_experiment,
    create_iteration,
    evaluate_iteration,
    post_results,
)

pytestmark = pytest.mark.django_db

EXACT_MATCH = "def evaluate(span):\n    return span.output == span.expected\n"

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


def _grade(experiment, candidate: dict, results: list[dict], *, order: int = 1):
    make_matrix_row(
        experiment.project,
        "Exact match",
        capability=experiment.capability,
        type=EvalMatrix.Type.DETERMINISTIC,
        source=EXACT_MATCH,
    )
    iteration = create_iteration(experiment, order=order, candidates=[candidate])
    posted = iteration.candidates.get()
    post_results(experiment, [{"candidate_id": str(posted.id), **r} for r in results])
    evaluate_iteration(experiment, order)
    posted.refresh_from_db()
    return posted


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
def test_the_eval_run_carries_the_candidate_identity(eager, mode, candidate, label, model_name):
    experiment = _experiment(mode=mode)
    graded = _grade(experiment, candidate, [{"datapoint_index": 0, "output": "a"}])

    run = graded.eval_run
    assert (run.params["label"], run.model_name) == (label, model_name)


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
