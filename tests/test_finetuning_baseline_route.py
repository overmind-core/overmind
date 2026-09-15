"""The baseline route follows the model, not the trainer: OpenRouter for anything its
catalog serves, our gateway for our deployments, a Modal base deploy for catalog bases
OpenRouter lacks, and a terminal UNAVAILABLE row when nothing can serve the model."""

from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from conftest import EVAL_ROWS, TRAIN_ROWS, frozen_dataset
from django.test import override_settings

from overbae.models import (
    Capability,
    EvalRun,
    EvalSet,
    EvalSetMember,
    Evaluator,
    FinetuningJob,
    FinetuningJobEval,
    FinetuningJobEvent,
    Project,
)
from overbae.services.finetuning_eval import (
    baseline_base_deploy_model,
    resolve_baseline_route,
    serialize_job_evals,
    sync_eval_scores,
    tick_job_evals,
)

pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("openrouter_catalog")]

GATEWAY = "https://gateway.example.modal.run"
OPENROUTER = "https://openrouter.ai/api/v1"


def _job(
    *,
    base_model: str = "Qwen/Qwen3-8B",
    incumbent: str = "",
    provider: str = FinetuningJob.Provider.MODAL,
) -> FinetuningJob:
    suffix = uuid.uuid4().hex[:6]
    project = Project.objects.create(name=f"route-{suffix}", slug=f"route-{suffix}")
    capability = Capability.objects.create(
        project=project, name="a", slug=f"a-{uuid.uuid4().hex[:6]}"
    )
    judge = Evaluator.objects.create(
        project=project,
        capability=capability,
        name=f"judge-{uuid.uuid4().hex[:6]}",
        kind=Evaluator.Kind.LLM_JUDGE,
        scope=Evaluator.Scope.FINAL_OUTPUT,
        rubric_md="Grade the output 0-1.",
        checklist=[{"id": "q1", "q": "Does the output satisfy the rubric?", "weight": 1.0}],
    )
    eset = EvalSet.objects.create(project=project, capability=capability, name="set")
    EvalSetMember.objects.create(
        eval_set=eset, evaluator=judge, role=EvalSetMember.Role.GENERATIVE, order=0
    )
    return FinetuningJob.objects.create(
        project=project,
        capability=capability,
        dataset=frozen_dataset(project, TRAIN_ROWS, name="train"),
        eval_dataset=frozen_dataset(project, EVAL_ROWS, name="eval"),
        eval_set=eset,
        base_model=base_model,
        status=FinetuningJob.Status.RUNNING,
        provider=provider,
        baseline_model=incumbent,
        hyperparameters={"eval_max_items": 3},
    )


def _tick_with_enqueue(job, django_capture_on_commit_callbacks):
    with (
        patch("overbae.tasks.eval.run_eval_run.apply_async") as apply,
        django_capture_on_commit_callbacks(execute=True),
    ):
        apply.return_value = MagicMock(id=f"celery-{uuid.uuid4().hex[:6]}")
        tick_job_evals(job, checkpoints=None)
    return apply


@override_settings(INFERENCE_API_URL=GATEWAY)
@pytest.mark.parametrize(
    "provider", [FinetuningJob.Provider.MODAL, FinetuningJob.Provider.TOGETHER_AI]
)
def test_open_weights_base_runs_baseline_via_openrouter(
    provider, django_capture_on_commit_callbacks
):
    """No incumbent → the untouched base is the baseline, scored through OpenRouter under
    the slug the catalog serves, whichever backend trains the job."""
    job = _job(provider=provider)

    apply = _tick_with_enqueue(job, django_capture_on_commit_callbacks)

    assert apply.call_count == 1
    row = FinetuningJobEval.objects.get(job=job, kind=FinetuningJobEval.Kind.BASELINE)
    assert row.status == FinetuningJobEval.Status.RUNNING
    assert row.model_id == "qwen/qwen3-8b"
    ref = row.eval_run.variants.get().model_ref
    assert ref.base_url == OPENROUTER
    assert ref.api_key_ref == "OPENROUTER_API_KEY"
    assert ref.model_id == "qwen/qwen3-8b"
    assert ref.params == {}
    assert baseline_base_deploy_model(job) is None


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_pinned_openrouter_id_wins_over_lowercased_catalog_id():
    """models.json ``openrouter_id`` covers slugs that are not the lower-cased id."""
    route = resolve_baseline_route(_job(base_model="Qwen/Qwen2.5-7B-Instruct"))

    assert route.kind == "openrouter"
    assert route.model_id == "qwen/qwen-2.5-7b-instruct"
    assert route.ready is True


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_open_weights_incumbent_resolves_to_served_slug():
    """A capability running Qwen in production names it with HF casing; OpenRouter is
    called with the slug it lists, not the raw string."""
    route = resolve_baseline_route(_job(incumbent="Qwen/Qwen3-8B"))

    assert route.kind == "openrouter"
    assert route.requested == "Qwen/Qwen3-8B"
    assert route.model_id == "qwen/qwen3-8b"
    assert route.label == "Current model · Qwen/Qwen3-8B"


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_catalog_base_openrouter_lacks_takes_modal_base_deploy(openrouter_catalog):
    openrouter_catalog.remove("qwen/qwen3-8b")
    job = _job()

    route = resolve_baseline_route(job)

    assert route.kind == "base_deploy"
    assert route.ready is False  # nothing deployed yet — the deploy task drives it
    assert "Waiting for base deployment" in route.detail
    assert baseline_base_deploy_model(job) == "Qwen/Qwen3-8B"
    with patch("overbae.tasks.eval.run_eval_run.apply_async") as apply:
        tick_job_evals(job, checkpoints=None)
    assert apply.call_count == 0
    assert not FinetuningJobEval.objects.filter(job=job).exists()  # waiting, not failed


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_unavailable_incumbent_lands_terminal_row_without_an_eval_run(
    django_capture_on_commit_callbacks,
):
    """A model nothing can serve (a Cursor composer model) fails at the baseline step
    with a persisted reason instead of an EvalRun that errors on every sample."""
    job = _job(incumbent="composer-2")

    apply = _tick_with_enqueue(job, django_capture_on_commit_callbacks)

    assert apply.call_count == 0
    assert EvalRun.objects.filter(project=job.project).count() == 0
    row = FinetuningJobEval.objects.get(job=job, kind=FinetuningJobEval.Kind.BASELINE)
    assert row.status == FinetuningJobEval.Status.UNAVAILABLE
    assert row.eval_run_id is None
    assert row.model_id == "composer-2"
    assert row.error_message == (
        "Model not available for evaluation: composer-2 is not served by OpenRouter "
        "or an Overmind deployment."
    )
    event = FinetuningJobEvent.objects.get(job=job)
    assert event.event_type == "log"
    assert "composer-2" in event.message
    assert event.data == {"baseline_model": "composer-2", "reason": "model_unavailable"}

    serialized = serialize_job_evals(job)
    assert serialized[0]["status"] == "unavailable"
    assert serialized[0]["error_message"] == row.error_message
    assert baseline_base_deploy_model(job) is None

    # Idempotent: the tick never re-creates or re-flags the row.
    apply = _tick_with_enqueue(job, django_capture_on_commit_callbacks)
    assert apply.call_count == 0
    assert FinetuningJobEval.objects.filter(job=job).count() == 1
    assert FinetuningJobEvent.objects.filter(job=job).count() == 1


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_unavailable_baseline_leaves_final_without_a_delta():
    job = _job(incumbent="composer-2")
    FinetuningJobEval.objects.create(
        job=job,
        kind=FinetuningJobEval.Kind.BASELINE,
        status=FinetuningJobEval.Status.UNAVAILABLE,
        model_id="composer-2",
        error_message="Model not available for evaluation",
    )
    final_run = EvalRun.objects.create(
        project=job.project,
        name="final",
        dataset=job.eval_dataset,
        eval_set=job.eval_set,
        status=EvalRun.Status.COMPLETED,
        summary={"variants": {"v": {"metrics": {"m": {"mean": 0.8}}}}},
    )
    FinetuningJobEval.objects.create(
        job=job,
        eval_run=final_run,
        kind=FinetuningJobEval.Kind.FINAL,
        status=FinetuningJobEval.Status.RUNNING,
        model_id="ft-x",
    )

    rows = {r["kind"]: r for r in sync_eval_scores(job)}

    assert rows["baseline"]["status"] == "unavailable"
    assert rows["baseline"]["aggregate_score"] is None
    assert rows["final"]["aggregate_score"] == pytest.approx(0.8)
    assert rows["final"]["baseline_delta"] is None
    job.refresh_from_db()
    assert job.progress["judge_evals"][0]["status"] == "unavailable"


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_unreachable_catalog_waits_instead_of_marking_unavailable(openrouter_catalog):
    openrouter_catalog.down()
    job = _job(incumbent="Qwen/Qwen3-8B")

    route = resolve_baseline_route(job)
    with patch("overbae.tasks.eval.run_eval_run.apply_async") as apply:
        tick_job_evals(job, checkpoints=None)

    assert route.kind == "openrouter"
    assert route.ready is False
    assert "unreachable" in route.detail
    assert apply.call_count == 0
    assert not FinetuningJobEval.objects.filter(job=job).exists()


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_curated_incumbent_resolves_while_catalog_is_down(openrouter_catalog):
    openrouter_catalog.down()

    route = resolve_baseline_route(_job(incumbent="claude-sonnet-5"))

    assert route.kind == "openrouter"
    assert route.model_id == "anthropic/claude-sonnet-5"
    assert route.ready is True


@override_settings(INFERENCE_API_URL=GATEWAY)
def test_deploy_task_does_no_gpu_work_for_openrouter_or_unavailable_baselines(
    django_capture_on_commit_callbacks,
):
    import modal

    from overbae.tasks.model_deployment import deploy_base_model_for_eval

    routed = _job()
    dead = _job(incumbent="composer-2")

    with (
        patch.object(modal.Cls, "from_name") as cls_from_name,
        patch("overbae.tasks.eval.run_eval_run.apply_async") as apply,
        django_capture_on_commit_callbacks(execute=True),
    ):
        apply.return_value = MagicMock(id="celery-route-1")
        deploy_base_model_for_eval(job_id=str(routed.id))
        deploy_base_model_for_eval(job_id=str(dead.id))

    cls_from_name.assert_not_called()
    assert apply.call_count == 1  # the OpenRouter baseline, launched by the tick
    assert FinetuningJobEval.objects.get(job=routed).status == FinetuningJobEval.Status.RUNNING
    assert FinetuningJobEval.objects.get(job=dead).status == FinetuningJobEval.Status.UNAVAILABLE
