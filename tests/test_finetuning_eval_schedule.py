from itertools import product
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from conftest import TRAIN_ROWS, frozen_dataset

from modal_shared.context_budget import DEFAULT_OUTPUT_TOKENS
from modal_shared.preparation import preparation_failure
from overbae.api.serializers import FinetuningJobSerializer
from overbae.models import (
    Capability,
    DeployedModel,
    EvalSet,
    EvalSetMember,
    Evaluator,
    FinetuningJob,
    FinetuningJobEval,
    Project,
    ProjectMembership,
    User,
)
from overbae.services import deployment, finetuning_eval
from overbae.services.finetuning_eval import (
    baseline_needs_base_deploy,
    reset_before_evals_for_retry,
    start_before_evals,
    sync_eval_scores,
    tick_job_evals,
)
from overbae.tasks import eval as eval_tasks
from overbae.tasks.finetuning import run_finetuning
from overbae.tasks.inference_controller import reconcile_deployments

pytestmark = pytest.mark.django_db
FIELDS = ("eval_incumbent_before", "eval_incumbent_after", "eval_model_before", "eval_model_after")


@pytest.fixture
def job(monkeypatch):
    monkeypatch.setattr("modal.Function.from_name", Mock())
    project = Project.objects.create(name="Schedule", slug="schedule")
    user = User.objects.create_user(
        email="schedule@example.test", password="test", clerk_user_id="schedule"
    )
    ProjectMembership.objects.create(project=project, user=user)
    capability = Capability.objects.create(
        project=project, name="Support", slug="support", model="openai/gpt-5.6-sol"
    )
    training = frozen_dataset(project, TRAIN_ROWS, capability=capability)
    evaluation = frozen_dataset(
        project,
        [
            {"input": "held-out-1", "expected_output": "answer-1"},
            {"input": "held-out-2", "expected_output": "answer-2"},
        ],
        capability=capability,
    )
    eval_set = EvalSet.objects.create(project=project, name="Quality")
    evaluator = Evaluator.objects.create(
        project=project,
        name="Match",
        kind=Evaluator.Kind.DETERMINISTIC,
        config={"check": "exact_match"},
    )
    EvalSetMember.objects.create(
        eval_set=eval_set, evaluator=evaluator, role=EvalSetMember.Role.GENERATIVE
    )
    monkeypatch.setattr(
        "overbae.tasks.eval.run_eval_run.apply_async",
        Mock(return_value=SimpleNamespace(id="eval-task")),
    )
    return FinetuningJob.objects.create(
        project=project,
        capability=capability,
        dataset=training,
        cell=training.active_cell,
        eval_dataset=evaluation,
        eval_cell=evaluation.active_cell,
        eval_set=eval_set,
        base_model="meta-llama/Llama-3.2-3B-Instruct",
        provider=FinetuningJob.Provider.TOGETHER_AI,
        triggered_by=user,
        baseline_model=capability.model,
        eval_incumbent_before=True,
        eval_model_before=False,
        status=FinetuningJob.Status.PREPARING,
    )


@pytest.mark.parametrize("choices", list(product([False, True], repeat=4)))
def test_every_schedule_runs_only_selected_models_at_the_selected_time(job, choices):
    for field, value in zip(FIELDS, choices, strict=True):
        setattr(job, field, value)
    job.save(update_fields=FIELDS)
    tick_job_evals(job)
    before = {
        kind
        for kind, enabled in (("baseline", choices[0]), ("model_before", choices[2]))
        if enabled
    }
    assert set(job.job_evals.values_list("kind", flat=True)) == before
    for row in job.job_evals.all():
        assert row.model_id == (job.baseline_model if row.kind == "baseline" else job.base_model)

    job.status = FinetuningJob.Status.SUCCEEDED
    job.output_model_name = "org/trained-model"
    job.save(update_fields=["status", "output_model_name"])
    tick_job_evals(job)
    after = {
        kind
        for kind, enabled in (("incumbent_after", choices[1]), ("final", choices[3]))
        if enabled
    }
    assert set(job.job_evals.values_list("kind", flat=True)) == before | after
    for row in job.job_evals.filter(kind__in=after):
        assert row.model_id == (
            job.baseline_model if row.kind == "incumbent_after" else job.output_model_name
        )
    tick_job_evals(job)
    assert job.job_evals.count() == len(before | after)


@pytest.mark.parametrize("hyperparameters", [{}, {"eval_max_items": 3}])
def test_every_training_evaluation_uses_the_complete_dataset(job, hyperparameters):
    source = [
        {"input": {"case": i, "mode": f"worker-{i // 300}"}, "expected_output": f"answer-{i}"}
        for i in range(900)
    ]
    evaluation = frozen_dataset(job.project, source, capability=job.capability)
    job.eval_dataset = evaluation
    job.eval_cell = evaluation.active_cell
    job.hyperparameters = hyperparameters
    for field in FIELDS:
        setattr(job, field, True)
    job.save()
    tick_job_evals(job)
    job.status = FinetuningJob.Status.SUCCEEDED
    job.output_model_name = "org/trained-model"
    job.save(update_fields=["status", "output_model_name"])
    tick_job_evals(job)

    assert set(job.job_evals.values_list("kind", flat=True)) == {
        "baseline",
        "model_before",
        "incumbent_after",
        "final",
    }
    for row in job.job_evals.select_related("eval_run").all():
        run = row.eval_run
        assert run.max_items == 0
        assert run.sampling == 1.0
        assert run.cell_id == evaluation.active_cell.id
        items = eval_tasks._resolve_items(run)
        assert [item["row_index"] for item in items] == list(range(900))
        assert [item["input"] for item in items] == [row["input"] for row in source]


@pytest.mark.parametrize("max_items,sampling", [(100, 1.0), (0, 0.5)])
def test_group_baseline_reuse_requires_full_dataset_coverage(job, max_items, sampling):
    job.group_id = job.id
    job.save(update_fields=["group_id"])
    tick_job_evals(job)
    sampled = job.job_evals.get(kind="baseline").eval_run
    sampled.max_items = max_items
    sampled.sampling = sampling
    sampled.save(update_fields=["max_items", "sampling"])
    sibling = FinetuningJob.objects.create(
        project=job.project,
        capability=job.capability,
        dataset=job.dataset,
        cell=job.cell,
        eval_dataset=job.eval_dataset,
        eval_cell=job.eval_cell,
        eval_set=job.eval_set,
        group_id=job.group_id,
        base_model=job.base_model,
        provider=job.provider,
        baseline_model=job.baseline_model,
        eval_incumbent_before=True,
        eval_model_before=False,
        status=job.status,
    )
    tick_job_evals(sibling)
    run = sibling.job_evals.get(kind="baseline").eval_run
    assert run.id != sampled.id
    assert run.max_items == 0
    assert run.sampling == 1.0
    sampled.refresh_from_db()
    assert (sampled.max_items, sampled.sampling) == (max_items, sampling)


def test_after_only_never_starts_a_baseline_job(job):
    job.provider = FinetuningJob.Provider.MODAL
    job.eval_incumbent_before = False
    job.eval_model_before = False
    job.eval_incumbent_after = True
    assert baseline_needs_base_deploy(job) is False
    start_before_evals(job)
    assert not job.job_evals.exists()


def test_retry_preserves_eval_results_and_resets_failed_before_links(job):
    tick_job_evals(job)
    row = job.job_evals.get(kind="baseline")
    run = row.eval_run
    row.status = FinetuningJobEval.Status.FAILED
    row.save(update_fields=["status"])
    job.progress = {"before_evals_started_at": 1}
    reset_before_evals_for_retry(job)
    assert not job.job_evals.exists()
    run.refresh_from_db()
    assert "before_evals_started_at" not in job.progress


def test_evaluation_choices_are_immutable_after_setup_starts(job):
    serializer = FinetuningJobSerializer(job, data={"eval_incumbent_before": False}, partial=True)
    assert not serializer.is_valid()
    assert "eval_incumbent_before" in serializer.errors


def test_baseline_selection_starts_without_waiting_for_completion(job):
    start_before_evals(job)
    row = job.job_evals.get(kind="baseline")
    assert row.status != FinetuningJobEval.Status.COMPLETED
    assert job.progress["before_evals_started_at"]


def test_submission_starts_training_while_baseline_eval_is_running(job, monkeypatch, settings):
    settings.FINETUNING_BACKEND = "together"
    runner = Mock()
    runner.submit.return_value = SimpleNamespace(remote_id="training-task", num_examples=None)
    monkeypatch.setattr("overbae.services.finetuning_runner.get_runner", lambda: runner)
    result = run_finetuning(job_id=str(job.id))
    job.refresh_from_db()
    assert result["status"] == job.status == "running"
    assert job.remote_job_id == "training-task"
    assert job.job_evals.get(kind="baseline").status != FinetuningJobEval.Status.COMPLETED
    runner.submit.assert_called_once()


def test_failed_baseline_eval_does_not_block_training(job, monkeypatch, settings):
    settings.FINETUNING_BACKEND = "together"
    tick_job_evals(job)
    job.job_evals.update(status=FinetuningJobEval.Status.FAILED)
    runner = Mock()
    runner.submit.return_value = SimpleNamespace(remote_id="training-task", num_examples=None)
    monkeypatch.setattr("overbae.services.finetuning_runner.get_runner", lambda: runner)
    result = run_finetuning(job_id=str(job.id))
    job.refresh_from_db()
    assert result["status"] == job.status == "running"
    runner.submit.assert_called_once()


def test_baseline_launch_error_does_not_block_training(job, monkeypatch, settings):
    settings.FINETUNING_BACKEND = "together"
    runner = Mock()
    runner.submit.return_value = SimpleNamespace(remote_id="training-task", num_examples=None)
    monkeypatch.setattr("overbae.services.finetuning_runner.get_runner", lambda: runner)
    monkeypatch.setattr(
        "overbae.services.finetuning_eval.start_before_evals",
        Mock(side_effect=RuntimeError("eval queue unavailable")),
    )

    result = run_finetuning(job_id=str(job.id))

    job.refresh_from_db()
    assert result["status"] == job.status == "running"
    runner.submit.assert_called_once()


@pytest.mark.parametrize("state", ["queued", "running"])
def test_launched_modal_job_prepares_data_before_gpu_submission(job, monkeypatch, settings, state):
    settings.FINETUNING_BACKEND = "modal"
    preparation = SimpleNamespace(id="preparation-id", state=state)
    monkeypatch.setattr("overbae.tasks.finetuning.for_job", lambda _: preparation)
    runner = Mock()
    monkeypatch.setattr("overbae.services.finetuning_runner.get_runner", lambda: runner)
    inspect = Mock()
    monkeypatch.setattr("overbae.tasks.finetuning.inspect_preparation.delay", inspect)
    resume = Mock(return_value=SimpleNamespace(id="resume-task"))
    monkeypatch.setattr(run_finetuning, "apply_async", resume)

    result = run_finetuning(job_id=str(job.id))

    job.refresh_from_db()
    assert result["status"] == job.status == "preparing"
    runner.submit.assert_not_called()
    assert inspect.call_count == (1 if state == "queued" else 0)
    resume.assert_called_once_with(kwargs={"job_id": str(job.id)}, countdown=15)


def test_preprocessing_worker_failure_preserves_actionable_error_and_never_submits_gpu(
    job, monkeypatch, settings
):
    settings.FINETUNING_BACKEND = "modal"
    failure = preparation_failure("worker_out_of_date")
    prep = SimpleNamespace(state="failed", error=failure["error"], report=failure)
    monkeypatch.setattr("overbae.tasks.finetuning.for_job", lambda _: prep)
    runner = Mock()
    monkeypatch.setattr("overbae.services.finetuning_runner.get_runner", lambda: runner)
    job.max_retries = 0
    job.save(update_fields=["max_retries"])

    result = run_finetuning(job_id=str(job.id))

    job.refresh_from_db()
    assert result["status"] == job.status == "failed"
    assert job.error_message == failure["error"]
    assert FinetuningJobSerializer(job).data["error_message"] == failure["error"]
    assert not job.remote_job_id and not job.job_evals.exists()
    runner.submit.assert_not_called()


@pytest.mark.parametrize("existing_waiter", [False, True])
@pytest.mark.parametrize("cancel_pending", [False, True])
def test_unresolved_baseline_does_not_change_running_training(
    job, monkeypatch, settings, existing_waiter, cancel_pending
):
    settings.FINETUNING_BACKEND = "modal"
    monkeypatch.setattr(
        "overbae.tasks.finetuning.for_job",
        lambda _: SimpleNamespace(state="ready", config={"context_length": 4096}),
    )
    job.provider = FinetuningJob.Provider.MODAL
    job.eval_incumbent_before = False
    job.eval_model_before = True
    job.max_retries = 0
    job.save()
    baseline = DeployedModel.objects.create(
        project=job.project,
        model_id=deployment.base_model_slug(deployment.get_hf_base(job.base_model)),
        status="failed",
        deployment_dispatching=True,
        deployment_cancel_pending=cancel_pending,
        deployment_call_id="fc-old" if cancel_pending else "",
    )
    if existing_waiter:
        baseline.deployment_waiters.add(job)
    runner = Mock()
    runner.submit.return_value = SimpleNamespace(remote_id="training-task", num_examples=None)
    monkeypatch.setattr("overbae.services.finetuning_runner.get_runner", lambda: runner)
    monkeypatch.setattr("overbae.tasks.model_deployment.deploy_base_model_for_eval.delay", Mock())
    cancel = Mock(return_value=False)
    monkeypatch.setattr(deployment, "cancel_operation", cancel)
    spawn = Mock()
    monkeypatch.setattr(deployment, "spawn_operation", spawn)

    assert run_finetuning(job_id=str(job.pk))["status"] == "running"
    deployment.ensure_baseline_deployment(str(job.pk))
    baseline.refresh_from_db()
    assert baseline.status == "failed"
    assert baseline.deployment_dispatching
    assert baseline.deployment_notify
    assert baseline.deployment_attempts == 0
    deployment.advance_deployment(baseline.pk)

    job.refresh_from_db()
    assert job.job_evals.get(kind="model_before").status == "failed"
    assert job.status == "running"
    assert not job.error_message
    runner.submit.assert_called_once()
    spawn.assert_not_called()
    assert cancel.call_count == int(cancel_pending)


def test_eval_score_sync_does_not_overwrite_newer_deployment_progress(job):
    saved = {
        "baseline_deployment": {"stage": "warm", "label": "Booting and verifying"},
        "activity": [{"ts": 1, "message": "Baseline evaluation: Booting and verifying"}],
        "train_loss": 0.4,
    }
    FinetuningJob.objects.filter(pk=job.pk).update(progress=saved)
    sync_eval_scores(job)
    job.refresh_from_db()
    assert job.progress == {**saved, "judge_evals": []}


def test_baseline_launch_is_idempotent(job):
    start_before_evals(job)
    start_before_evals(job)
    job.refresh_from_db()
    assert job.job_evals.filter(kind="baseline").count() == 1


def test_deployment_controller_recovers_baseline_while_training_runs(job, monkeypatch):
    job.status = FinetuningJob.Status.RUNNING
    job.progress = {"before_evals_started_at": 1}
    job.save(update_fields=["status", "progress"])
    ensure = Mock()
    monkeypatch.setattr("overbae.tasks.inference_controller.ensure_baseline_deployment", ensure)

    reconcile_deployments()

    ensure.assert_called_once_with(str(job.id))


@pytest.mark.parametrize("provider", ["modal", "baseten", "together_ai"])
@pytest.mark.parametrize("incumbent", [False, True])
def test_starting_model_uses_openrouter_without_provisioning_inference(
    job, monkeypatch, provider, incumbent, django_capture_on_commit_callbacks
):
    job.provider = provider
    job.base_model = "Qwen/Qwen3.5-9B"
    job.eval_incumbent_before = incumbent
    job.eval_model_before = not incumbent
    job.baseline_model = ""
    job.capability = None
    job.save()
    resolver = Mock(return_value="qwen/qwen3.5-9b")
    monkeypatch.setattr(finetuning_eval, "resolve_training_openrouter_slug", resolver)
    deploy = Mock()
    monkeypatch.setattr("overbae.tasks.model_deployment.deploy_base_model_for_eval.delay", deploy)
    with django_capture_on_commit_callbacks(execute=True):
        start_before_evals(job)
    row = job.job_evals.get(kind="baseline" if incumbent else "model_before")
    ref = row.eval_run.variants.get().model_ref
    assert row.model_id == ref.model_id == "qwen/qwen3.5-9b"
    assert ref.base_url == "https://openrouter.ai/api/v1"
    assert ref.api_key_ref == "OPENROUTER_API_KEY"
    assert ref.params == {"max_tokens": DEFAULT_OUTPUT_TOKENS}
    assert not baseline_needs_base_deploy(job)
    assert deployment.ensure_baseline_deployment(str(job.pk)) is None
    assert not DeployedModel.objects.exists()
    deploy.assert_not_called()

    resolver.return_value = None
    assert not baseline_needs_base_deploy(job)
    start_before_evals(job)
    assert job.job_evals.count() == 1
    assert not DeployedModel.objects.exists()
    deploy.assert_not_called()


def test_missing_openrouter_model_still_prepares_local_base(job, monkeypatch):
    job.provider = "modal"
    job.eval_model_before = True
    job.eval_incumbent_before = False
    job.save()
    deploy = Mock()
    monkeypatch.setattr("overbae.tasks.model_deployment.deploy_base_model_for_eval.delay", deploy)
    start_before_evals(job)
    deploy.assert_called_once_with(job_id=str(job.pk))
    baseline = deployment.ensure_baseline_deployment(str(job.pk))
    assert baseline.deployment_stage == "base"
    assert baseline.status == "queued"
    assert not job.job_evals.exists()

    monkeypatch.setattr(
        finetuning_eval, "resolve_training_openrouter_slug", Mock(return_value="provider/model")
    )
    assert baseline_needs_base_deploy(job)


def test_eval_route_is_not_resolved_again_after_choosing_model(job, monkeypatch):
    job.provider = "modal"
    job.eval_model_before = True
    job.eval_incumbent_before = False
    job.save()
    resolver = Mock(side_effect=["qwen/qwen3.5-9b", None])
    monkeypatch.setattr(finetuning_eval, "resolve_training_openrouter_slug", resolver)
    finetuning_eval.ensure_target_eval(job, kind="model_before")
    ref = job.job_evals.get(kind="model_before").eval_run.variants.get().model_ref
    assert ref.model_id == "qwen/qwen3.5-9b"
    assert ref.api_key_ref == "OPENROUTER_API_KEY"
    resolver.assert_called_once()


def test_retry_can_use_openrouter_instead_of_failed_baseline_deployment(job, monkeypatch):
    baseline = DeployedModel.objects.create(
        project=job.project,
        model_id="base--failed",
        status="failed",
        deployment_dispatching=True,
    )
    baseline.deployment_waiters.add(job)
    FinetuningJobEval.objects.create(job=job, kind="model_before", status="failed")
    monkeypatch.setattr(
        deployment, "resolve_training_openrouter_slug", Mock(return_value="provider/model")
    )
    reset_before_evals_for_retry(job)
    baseline.refresh_from_db()
    assert baseline.status == "failed"
    assert baseline.deployment_dispatching
    assert not baseline.deployment_waiters.exists()
    assert not job.job_evals.exists()


def test_openrouter_starting_model_does_not_change_trained_checkpoint_route(job, monkeypatch):
    job.provider = "modal"
    job.eval_model_before = True
    job.eval_incumbent_before = False
    job.save()
    monkeypatch.setattr(
        finetuning_eval, "resolve_training_openrouter_slug", Mock(return_value="provider/base")
    )
    tick_job_evals(job)
    before = job.job_evals.get(kind="model_before")
    assert before.eval_run.variants.get().model_ref.api_key_ref == "OPENROUTER_API_KEY"
    job.status = "succeeded"
    job.output_model_name = "trained-checkpoint"
    job.save()
    tick_job_evals(job)
    assert not job.job_evals.filter(kind="final").exists()
    deployed = DeployedModel.objects.create(
        project=job.project,
        finetuning_job=job,
        model_id="ft-trained",
        status="ready",
        inference_url="https://inference.test",
    )
    tick_job_evals(job)
    after = job.job_evals.get(kind="final")
    assert after.model_id == deployed.model_id
    assert after.eval_run.variants.get().model_ref.api_key_ref == "INFERENCE_API_KEY"
    assert after.eval_run.cell_id == before.eval_run.cell_id
    assert (
        after.eval_run.run_evaluators.get().snapshot
        == before.eval_run.run_evaluators.get().snapshot
    )


def test_base_before_can_supply_the_comparison_without_incumbent_eval(job):
    job.eval_incumbent_before = False
    job.eval_model_before = True
    job.save(update_fields=["eval_incumbent_before", "eval_model_before"])
    tick_job_evals(job)
    before = job.job_evals.get(kind="model_before")
    before.aggregate_score = 0.4
    before.save(update_fields=["aggregate_score"])
    after = FinetuningJobEval.objects.create(job=job, kind="final", aggregate_score=0.7)
    sync_eval_scores(job)
    after.refresh_from_db()
    assert after.baseline_delta == pytest.approx(0.3)


def test_after_evals_reuse_the_pinned_data_and_graders(job):
    tick_job_evals(job)
    before = job.job_evals.get(kind="baseline").eval_run
    evaluator = job.eval_set.members.get().evaluator
    evaluator.config = {"check": "contains"}
    evaluator.save(update_fields=["config"])
    job.status = FinetuningJob.Status.SUCCEEDED
    job.output_model_name = "org/trained-model"
    job.eval_incumbent_after = True
    tick_job_evals(job)
    for row in job.job_evals.exclude(kind="baseline").select_related("eval_run"):
        assert row.eval_run.cell_id == before.cell_id
        assert row.eval_run.run_evaluators.get().snapshot == before.run_evaluators.get().snapshot


def serializer_for(job, **choices):
    return FinetuningJobSerializer(
        data={
            "project": str(job.project_id),
            "capability": str(job.capability_id) if job.capability_id else None,
            "dataset": str(job.dataset_id),
            "eval_dataset": str(job.eval_dataset_id),
            "eval_set": str(job.eval_set_id),
            "base_model": job.base_model,
            **choices,
        },
        context={"request": SimpleNamespace(user=job.triggered_by)},
    )


def test_api_persists_all_four_choices_and_incumbent_snapshot(job):
    serializer = serializer_for(job, **dict.fromkeys(FIELDS, True))
    assert serializer.is_valid(), serializer.errors
    created = serializer.save()
    assert all(getattr(created, field) for field in FIELDS)
    assert created.baseline_model == job.capability.model


def test_new_job_uses_codebase_benchmark_even_when_live_model_is_deleted(job):
    live = DeployedModel.objects.create(
        project=job.project, finetuning_job=job, model_id="ft-old-live", status="deleted"
    )
    job.capability.active_model = live
    job.capability.save(update_fields=["active_model"])
    serializer = serializer_for(job, eval_incumbent_before=True)
    assert serializer.is_valid(), serializer.errors
    assert serializer.save().baseline_model == job.capability.model


def test_new_job_pins_selected_trained_benchmark_for_before_and_after_evals(job):
    benchmark = DeployedModel.objects.create(
        project=job.project,
        finetuning_job=job,
        model_id="ft-benchmark",
        status="ready",
        inference_url="https://inference.test",
    )
    job.capability.benchmark_model = benchmark
    job.capability.save(update_fields=["benchmark_model"])
    serializer = serializer_for(
        job,
        eval_incumbent_before=True,
        eval_incumbent_after=True,
        eval_model_before=False,
        eval_model_after=False,
    )
    assert serializer.is_valid(), serializer.errors
    created = serializer.save()
    assert created.baseline_model == benchmark.model_id
    job.capability.benchmark_model = None
    job.capability.save(update_fields=["benchmark_model"])
    created.refresh_from_db()
    assert finetuning_eval.resolve_baseline_model(created) == benchmark.model_id
    tick_job_evals(created)
    created.status = "succeeded"
    created.save(update_fields=["status"])
    tick_job_evals(created)
    assert set(created.job_evals.values_list("kind", "model_id")) == {
        ("baseline", benchmark.model_id),
        ("incumbent_after", benchmark.model_id),
    }


def test_unavailable_benchmark_cannot_start_incumbent_evaluations(job):
    benchmark = DeployedModel.objects.create(
        project=job.project, finetuning_job=job, model_id="ft-unavailable", status="deleted"
    )
    job.capability.benchmark_model = benchmark
    job.capability.save(update_fields=["benchmark_model"])
    serializer = serializer_for(job, eval_incumbent_before=True)
    assert not serializer.is_valid()
    assert "benchmark model is unavailable" in str(serializer.errors)
    serializer = serializer_for(job, eval_incumbent_before=False, eval_incumbent_after=False)
    assert serializer.is_valid(), serializer.errors


def test_run_specific_benchmark_is_pinned_without_changing_capability(job):
    benchmark = DeployedModel.objects.create(
        project=job.project,
        finetuning_job=job,
        model_id="ft-run-benchmark",
        status="ready",
        inference_url="https://inference.test",
    )
    serializer = serializer_for(
        job,
        baseline_model=benchmark.model_id,
        eval_incumbent_before=True,
        eval_incumbent_after=True,
        eval_model_before=False,
        eval_model_after=False,
    )
    assert serializer.is_valid(), serializer.errors
    created = serializer.save()
    job.capability.refresh_from_db()
    assert job.capability.benchmark_model_id is None
    assert job.capability.active_model_id is None
    assert created.baseline_model == benchmark.model_id
    tick_job_evals(created)
    created.status = "succeeded"
    created.save(update_fields=["status"])
    tick_job_evals(created)
    assert set(created.job_evals.values_list("kind", "model_id")) == {
        ("baseline", benchmark.model_id),
        ("incumbent_after", benchmark.model_id),
    }


def test_run_can_select_codebase_incumbent_over_capability_default(job):
    benchmark = DeployedModel.objects.create(
        project=job.project, finetuning_job=job, model_id="ft-default", status="ready"
    )
    job.capability.benchmark_model = benchmark
    job.capability.save(update_fields=["benchmark_model"])
    serializer = serializer_for(job, baseline_model=job.capability.model)
    assert serializer.is_valid(), serializer.errors
    assert serializer.save().baseline_model == job.capability.model
    job.capability.refresh_from_db()
    assert job.capability.benchmark_model_id == benchmark.id


@pytest.mark.parametrize("kind", ["foreign", "infrastructure", "deleted", "unknown", "blank"])
def test_run_rejects_invalid_benchmark_selection(job, kind):
    model_id = "" if kind == "blank" else "ft-benchmark"
    if kind not in {"unknown", "blank"}:
        DeployedModel.objects.create(
            project=Project.objects.create(name="other") if kind == "foreign" else job.project,
            finetuning_job=None if kind == "infrastructure" else job,
            model_id=model_id,
            status="deleted" if kind == "deleted" else "ready",
        )
    serializer = serializer_for(job, baseline_model=model_id)
    assert not serializer.is_valid()
    assert "baseline_model" in serializer.errors


def test_run_benchmark_cannot_change_after_creation(job):
    serializer = FinetuningJobSerializer(
        job,
        data={"baseline_model": "ft-replacement"},
        partial=True,
        context={"request": SimpleNamespace(user=job.triggered_by)},
    )
    assert not serializer.is_valid()
    assert "baseline_model" in serializer.errors


def test_run_without_capability_can_benchmark_a_trained_model(job):
    benchmark = DeployedModel.objects.create(
        project=job.project, finetuning_job=job, model_id="ft-unbound-benchmark", status="ready"
    )
    job.capability = None
    serializer = serializer_for(job, baseline_model=benchmark.model_id, eval_incumbent_before=True)
    assert serializer.is_valid(), serializer.errors
    created = serializer.save()
    assert created.baseline_model == benchmark.model_id
    assert created.capability_id is None
    assert created.eval_incumbent_before


def test_no_incumbent_defaults_to_base_before_and_trained_after(job):
    job.capability = None
    serializer = serializer_for(job)
    assert serializer.is_valid(), serializer.errors
    created = serializer.save()
    assert [getattr(created, field) for field in FIELDS] == [False, False, True, True]


def test_matched_base_is_default_even_when_an_incumbent_exists(job):
    serializer = serializer_for(job)
    assert serializer.is_valid(), serializer.errors
    created = serializer.save()
    assert [getattr(created, field) for field in FIELDS] == [False, False, True, True]


def test_overlap_is_recorded_without_blocking_submission(job, monkeypatch, settings):
    settings.FINETUNING_BACKEND = "together"
    runner = Mock()
    runner.submit.return_value = SimpleNamespace(remote_id="training-task", num_examples=None)
    monkeypatch.setattr("overbae.services.finetuning_runner.get_runner", lambda: runner)
    overlap = Mock(return_value={"overlap_count": 2})
    monkeypatch.setattr("overbae.services.datasets.rows.contamination", overlap)
    assert run_finetuning(job_id=str(job.id))["status"] == "running"
    assert overlap.call_args.args[1].id == job.eval_cell_id
    assert job.events.filter(data__overlap_count=2).exists()
    runner.submit.assert_called_once()


def test_before_after_reuses_prompt_snapshot_and_prefers_matched_base(job):
    job.eval_model_before = True
    job.capability.improvement_metadata = {"system_prompt": "Pinned instruction"}
    job.capability.save()
    job.save()
    tick_job_evals(job)
    job.capability.improvement_metadata = {"system_prompt": "Later instruction"}
    job.capability.save()
    job.status = "succeeded"
    job.output_model_name = "trained-model"
    job.save()
    tick_job_evals(job)
    assert finetuning_eval.comparison_eval(job).kind == "model_before"
    for row in job.job_evals.select_related("eval_run"):
        assert row.eval_run.variants.get().params["system_prompt"] == "Pinned instruction"


def test_cannot_request_an_incumbent_eval_without_an_incumbent(job):
    job.capability = None
    serializer = serializer_for(job, eval_incumbent_after=True)
    assert not serializer.is_valid()
    assert "eval_incumbent_after" in serializer.errors


def test_all_evaluations_can_be_disabled(job):
    serializer = serializer_for(job, **dict.fromkeys(FIELDS, False))
    assert serializer.is_valid(), serializer.errors
    created = serializer.save()
    tick_job_evals(created)
    assert not created.job_evals.exists()
    start_before_evals(created)
    assert not created.job_evals.exists()


@pytest.mark.parametrize("field", ["eval_dataset", "eval_set"])
def test_evaluation_inputs_remain_required_with_all_checks_off(job, field):
    serializer = serializer_for(job, **dict.fromkeys(FIELDS, False))
    serializer.initial_data.pop(field)
    assert not serializer.is_valid()
    assert field in serializer.errors
