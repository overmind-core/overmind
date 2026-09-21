import uuid
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from conftest import TRAIN_ROWS, frozen_dataset
from django.utils import timezone
from modal.call_graph import InputStatus

from overbae.models import DeployedModel, FinetuningJob, Project
from overbae.services import deployment
from overbae.tasks.inference_controller import reconcile_deployments

pytestmark = pytest.mark.django_db


@pytest.fixture
def deployed():
    project = Project.objects.create(name="Deployment recovery")
    dataset = frozen_dataset(project, TRAIN_ROWS, name="train")
    job = FinetuningJob.objects.create(
        project=project,
        dataset=dataset,
        base_model="Qwen/Qwen3-8B",
        provider="modal",
        status="deploying",
        remote_job_id="run:call",
        result={"checkpoint": "preserved"},
    )
    return deployment.ensure_training_deployment(str(job.pk))


def advance(row):
    DeployedModel.objects.filter(pk=row.pk).update(deployment_next_poll_at=timezone.now())
    deployment.advance_deployment(row.pk)
    row.refresh_from_db()


def warming(row):
    DeployedModel.objects.filter(pk=row.pk).update(
        deployment_stage="warm",
        status="warming",
        deployment_call_id="fc-warm",
        weights_path="/weights/base",
        adapter_path="/weights/.adapters/ft-test",
        lora_rank=32,
        inference_url="https://inference.test",
    )
    row.refresh_from_db()


def test_replacement_worker_reconnects_to_boot_without_launching_gpu_work(deployed):
    warming(deployed)
    DeployedModel.objects.filter(pk=deployed.pk).update(
        deployment_claim=uuid.uuid4(),
        deployment_claim_until=timezone.now() - timedelta(seconds=1),
    )
    with (
        patch.object(deployment, "spawn_operation") as spawn,
        patch.object(
            deployment, "poll_operation", side_effect=[("pending", None), ("complete", None)]
        ) as poll,
    ):
        advance(deployed)
        assert deployed.deployment_call_id == "fc-warm"
        advance(deployed)
    assert deployed.status == "ready"
    assert poll.call_count == 2
    poll.assert_called_with("fc-warm")
    spawn.assert_not_called()
    assert deployed.finetuning_job.result == {"checkpoint": "preserved"}
    assert deployed.adapter_path == "/weights/.adapters/ft-test"


def test_one_claim_launches_one_operation(deployed):
    def spawn(row):
        deployment.advance_deployment(row.pk)
        return "fc-one"

    with patch.object(deployment, "spawn_operation", side_effect=spawn) as launch:
        advance(deployed)
    launch.assert_called_once()
    assert deployed.deployment_call_id == "fc-one"
    assert deployed.deployment_claim is None


def test_unacknowledged_submission_never_blindly_relaunches(deployed):
    DeployedModel.objects.filter(pk=deployed.pk).update(deployment_dispatching=True)
    with patch.object(deployment, "spawn_operation") as spawn:
        advance(deployed)
        advance(deployed)
    assert deployed.status == "failed"
    spawn.assert_not_called()
    with pytest.raises(ValueError, match="unresolved"):
        deployment.retry_deployment(deployed.pk)


def test_late_acknowledgement_is_retained_for_cancellation(deployed):
    def spawn(row):
        DeployedModel.objects.filter(pk=row.pk).update(
            status="failed",
            deployment_claim=None,
            deployment_claim_until=None,
        )
        return "fc-late"

    with patch.object(deployment, "spawn_operation", side_effect=spawn):
        advance(deployed)
    assert deployed.deployment_call_id == "fc-late"
    assert deployed.deployment_cancel_pending


def test_superseded_generation_cannot_publish_ready(deployed):
    warming(deployed)

    def poll(_call_id):
        DeployedModel.objects.filter(pk=deployed.pk).update(
            deployment_generation=uuid.uuid4(),
            status="deleted",
        )
        return "complete", None

    with patch.object(deployment, "poll_operation", side_effect=poll):
        advance(deployed)
    assert deployed.status == "deleted"
    assert deployed.deployed_at is None


def test_cancellation_during_poll_cannot_publish_ready(deployed):
    warming(deployed)

    def poll(_call_id):
        FinetuningJob.objects.filter(pk=deployed.finetuning_job_id).update(status="cancelled")
        return "complete", None

    with patch.object(deployment, "poll_operation", side_effect=poll):
        advance(deployed)
    assert deployed.status == "failed"
    assert deployed.deployed_at is None
    deployed.finetuning_job.refresh_from_db()
    assert deployed.finetuning_job.status == "cancelled"


def test_confirmed_failures_have_a_durable_retry_budget(deployed):
    deadline = deployed.deployment_deadline
    with patch.object(deployment, "poll_operation", return_value=("failed", None)):
        for attempt in range(1, deployment.MAX_ATTEMPTS + 1):
            DeployedModel.objects.filter(pk=deployed.pk).update(deployment_call_id=f"fc-{attempt}")
            advance(deployed)
            assert deployed.deployment_deadline == deadline
    assert deployed.status == "failed"
    assert deployed.deployment_attempts == deployment.MAX_ATTEMPTS
    assert "Retry limit" in deployed.error_message
    with patch.object(deployment, "spawn_operation") as spawn:
        for _ in range(3):
            advance(deployed)
        spawn.assert_not_called()
    deployed.finetuning_job.refresh_from_db()
    assert deployed.finetuning_job.status == "succeeded"
    assert deployed.finetuning_job.result == {"checkpoint": "preserved"}


def test_warm_failure_revalidates_base_before_retry(deployed):
    warming(deployed)
    with patch.object(deployment, "poll_operation", return_value=("failed", None)):
        advance(deployed)
    assert deployed.deployment_stage == "base"
    assert deployed.deployment_attempts == 2
    assert 25 < (deployed.deployment_next_poll_at - timezone.now()).total_seconds() <= 30
    assert deployed.adapter_path
    assert deployed.weights_path


def test_poll_transport_error_keeps_handle_and_budget(deployed):
    warming(deployed)
    with patch.object(deployment, "poll_operation", side_effect=ConnectionError("offline")):
        advance(deployed)
    assert deployed.deployment_call_id == "fc-warm"
    assert deployed.deployment_attempts == 1
    assert deployed.status == "warming"


def test_deadline_stops_remote_operation_without_losing_weights(deployed):
    warming(deployed)
    DeployedModel.objects.filter(pk=deployed.pk).update(
        deployment_deadline=timezone.now() - timedelta(seconds=1),
    )
    advance(deployed)
    assert deployed.status == "failed"
    assert deployed.deployment_cancel_pending
    with patch("modal.FunctionCall.from_id") as call:
        call.return_value.cancel.aio = AsyncMock()
        call.return_value.get_call_graph.aio = AsyncMock(
            return_value=[
                SimpleNamespace(
                    function_call_id="fc-warm",
                    status=InputStatus.TERMINATED,
                    children=[],
                )
            ]
        )
        advance(deployed)
        call.assert_called_with("fc-warm")
        call.return_value.cancel.aio.assert_awaited_once()
    assert not deployed.deployment_cancel_pending
    assert deployed.weights_path == "/weights/base"


def test_complete_adapter_pipeline_persists_every_stage(deployed):
    results = {
        "base": {},
        "archive": {},
        "weights": {"base_path": "bases/qwen", "lora_rank": 32},
        "register": "https://inference.test",
        "warm": None,
    }
    with (
        patch.object(
            deployment, "spawn_operation", side_effect=lambda row: "fc-" + row.deployment_stage
        ),
        patch.object(
            deployment,
            "poll_operation",
            side_effect=lambda handle: ("complete", results[handle.removeprefix("fc-")]),
        ),
        patch("overbae.services.finetuning_eval.tick_job_evals") as tick,
    ):
        for stage in results:
            assert deployed.deployment_stage == stage
            advance(deployed)
            assert deployed.deployment_call_id == "fc-" + stage
            advance(deployed)
        assert deployed.status == "ready"
        advance(deployed)
    assert deployed.quantization == "bf16"
    assert deployed.adapter_path == "/weights/.adapters/" + deployed.model_id
    assert deployed.lora_rank == 32
    tick.assert_called_once()


@pytest.mark.parametrize("adapter", [False, True])
def test_boot_always_verifies_persisted_adapter(deployed, adapter):
    warming(deployed)
    if not adapter:
        deployed.adapter_path = ""
    with patch("modal.Function.from_name") as fn:
        fn.return_value.spawn.aio = AsyncMock(return_value=SimpleNamespace(object_id="fc-boot"))
        assert deployment.spawn_operation(deployed) == "fc-boot"
    kwargs = fn.return_value.spawn.aio.call_args.kwargs
    assert kwargs["adapter"] == ((deployed.model_id, ".adapters/ft-test") if adapter else None)
    assert kwargs["lora_rank"] == (32 if adapter else 0)


@pytest.mark.parametrize("remote_failed", [False, True])
def test_provider_must_confirm_failure_before_new_attempt(remote_failed):
    with patch("modal.FunctionCall.from_id") as call:
        call.return_value.get.aio = AsyncMock(side_effect=RuntimeError("secret provider details"))
        call.return_value.get_call_graph.aio = AsyncMock(
            return_value=[
                SimpleNamespace(
                    function_call_id="fc-x",
                    status=InputStatus.FAILURE if remote_failed else InputStatus.PENDING,
                    children=[],
                )
            ]
        )
        if remote_failed:
            assert deployment.poll_operation("fc-x") == ("failed", "Remote operation failed.")
        else:
            with pytest.raises(RuntimeError):
                deployment.poll_operation("fc-x")


def test_controller_recovers_training_and_baseline_on_same_tick(deployed):
    baseline = DeployedModel.objects.create(
        project=deployed.project, model_id="base--qwen", status="warming"
    )
    with patch("overbae.tasks.model_deployment.advance_model_deployment.delay") as enqueue:
        assert reconcile_deployments() == 2
    assert {call.kwargs["deployment_id"] for call in enqueue.call_args_list} == {
        str(deployed.pk),
        str(baseline.pk),
    }


def test_retry_resets_generation_only_on_explicit_request(deployed):
    old_generation = deployed.deployment_generation
    DeployedModel.objects.filter(pk=deployed.pk).update(status="failed", deployment_attempts=3)
    deployment.ensure_training_deployment(str(deployed.finetuning_job_id))
    deployed.refresh_from_db()
    assert deployed.status == "failed"
    retried = deployment.retry_deployment(deployed.pk)
    assert retried.deployment_generation != old_generation
    assert retried.deployment_attempts == 1
    assert retried.status == "queued"


def test_baseline_notifies_all_waiting_jobs_without_training_link(deployed):
    job = deployed.finetuning_job
    baseline = DeployedModel.objects.create(
        project=deployed.project,
        model_id="base--test",
        status="ready",
        deployment_stage="ready",
        deployment_notify=True,
    )
    baseline.deployment_waiters.add(job)
    with patch("overbae.services.finetuning_eval.tick_job_evals") as tick:
        advance(baseline)
    tick.assert_called_once()
    assert tick.call_args.args[0].pk == job.pk
    assert not baseline.deployment_notify


def test_notification_is_retried_after_worker_failure(deployed):
    DeployedModel.objects.filter(pk=deployed.pk).update(status="ready", deployment_notify=True)
    with patch(
        "overbae.services.finetuning_eval.tick_job_evals",
        side_effect=[RuntimeError("broker down"), []],
    ) as tick:
        advance(deployed)
        assert deployed.deployment_notify
        advance(deployed)
    assert tick.call_count == 2
    assert not deployed.deployment_notify


def test_merged_weights_and_baseline_preserve_actual_quantization(deployed):
    FinetuningJob.objects.filter(pk=deployed.finetuning_job_id).update(provider="baseten")
    DeployedModel.objects.filter(pk=deployed.pk).update(
        deployment_stage="weights",
        deployment_call_id="fc-weights",
    )
    with patch.object(
        deployment,
        "poll_operation",
        return_value=(
            "complete",
            {
                "weights_path": "/weights/private",
                "quantization": "bf16",
                "num_parameters": 100,
            },
        ),
    ):
        advance(deployed)
    assert deployed.deployment_stage == "register"
    assert deployed.quantization == "bf16"
    assert not deployed.is_lora
    assert deployed.adapter_path == ""


def test_failed_baseline_does_not_reset_budget_for_same_waiter(deployed):
    job = deployed.finetuning_job
    baseline = DeployedModel.objects.create(
        project=job.project,
        model_id=deployment.base_model_slug(deployment.get_hf_base(job.base_model)),
        status="failed",
        deployment_stage="warm",
        deployment_attempts=3,
    )
    baseline.deployment_waiters.add(job)
    with (
        patch("overbae.services.finetuning_eval.job_wants_evals", return_value=True),
        patch(
            "overbae.services.finetuning_eval.baseline_needs_base_deploy",
            return_value=True,
        ),
    ):
        for _ in range(3):
            deployment.ensure_baseline_deployment(str(job.pk))
    baseline.refresh_from_db()
    assert baseline.status == "failed"
    assert baseline.deployment_attempts == 3


@pytest.mark.parametrize("status", ["ready", "warming"])
def test_shared_baseline_resizes_after_the_existing_operation_finishes(deployed, status):
    job = deployed.finetuning_job
    baseline = DeployedModel.objects.create(
        project=job.project,
        model_id=deployment.base_model_slug(deployment.get_hf_base(job.base_model)),
        status=status,
        deployment_stage="ready" if status == "ready" else "warm",
        max_model_len=4096,
        inference_url="https://inference.test",
    )
    generation = baseline.deployment_generation
    with (
        patch("overbae.services.finetuning_eval.job_wants_evals", return_value=True),
        patch("overbae.services.finetuning_eval.baseline_needs_base_deploy", return_value=True),
        patch("overbae.services.finetuning_eval.tick_job_evals") as tick,
    ):
        deployment.ensure_baseline_deployment(str(job.pk))
        baseline.refresh_from_db()
        if status == "warming":
            assert baseline.status == "warming"
            assert baseline.max_model_len == 4096
            assert baseline.deployment_generation == generation
            DeployedModel.objects.filter(pk=baseline.pk).update(
                status="ready", deployment_stage="ready", deployment_notify=True
            )
            advance(baseline)
    baseline.refresh_from_db()
    assert baseline.status == "queued"
    assert baseline.max_model_len == deployed.max_model_len
    assert baseline.deployment_generation != generation
    tick.assert_not_called()


def test_cancellation_acknowledgement_must_be_terminal_before_retry(deployed):
    warming(deployed)
    DeployedModel.objects.filter(pk=deployed.pk).update(
        status="failed", deployment_cancel_pending=True
    )
    with patch.object(deployment, "cancel_operation", return_value=False):
        advance(deployed)
    assert deployed.deployment_cancel_pending
    with pytest.raises(ValueError, match="unresolved"):
        deployment.retry_deployment(deployed.pk)


def test_stage_progress_preserves_training_metrics_without_duplicate_activity(deployed):
    job = deployed.finetuning_job
    FinetuningJob.objects.filter(pk=job.pk).update(progress={"train_loss": 0.4})
    warming(deployed)
    with patch.object(deployment, "poll_operation", return_value=("pending", None)):
        advance(deployed)
        advance(deployed)
    job.refresh_from_db()
    assert job.progress["train_loss"] == 0.4
    assert job.progress["deployment"]["label"] == "Booting and verifying"
    assert len(job.progress["activity"]) == 1


def test_baseline_progress_reaches_waiters_without_changing_training_deployment(deployed):
    job = deployed.finetuning_job
    FinetuningJob.objects.filter(pk=job.pk).update(status="running", progress={"train_loss": 0.4})
    baseline = DeployedModel.objects.create(
        project=job.project,
        model_id="base--progress",
        deployment_stage="warm",
        deployment_call_id="fc-baseline",
        status="warming",
    )
    baseline.deployment_waiters.add(job)
    with patch.object(deployment, "poll_operation", return_value=("pending", None)):
        advance(baseline)
        advance(baseline)
    job.refresh_from_db()
    assert job.progress["train_loss"] == 0.4
    assert "deployment" not in job.progress
    assert job.progress["baseline_deployment"]["label"] == "Booting and verifying"
    assert len(job.progress["activity"]) == 1
    assert job.progress["activity"][0]["message"] == ("Baseline evaluation: Booting and verifying")
    assert "fc-baseline" not in str(job.progress)

    FinetuningJob.objects.filter(pk=job.pk).update(status="cancelled")
    saved = job.progress
    advance(baseline)
    job.refresh_from_db()
    assert job.progress == saved


def test_confirmed_stopped_baseline_can_start_fresh_for_a_new_job(deployed):
    job = deployed.finetuning_job
    baseline = DeployedModel.objects.create(
        project=job.project,
        model_id=deployment.base_model_slug(deployment.get_hf_base(job.base_model)),
        status="failed",
        deployment_stage="base",
        deployment_attempts=3,
        weights_path="/weights/preserved",
    )
    previous_generation = baseline.deployment_generation
    with (
        patch("overbae.services.finetuning_eval.job_wants_evals", return_value=True),
        patch("overbae.services.finetuning_eval.baseline_needs_base_deploy", return_value=True),
        patch.object(deployment, "spawn_operation") as spawn,
    ):
        deployment.ensure_baseline_deployment(str(job.pk))
    baseline.refresh_from_db()
    assert baseline.status == "queued"
    assert baseline.deployment_stage == "base"
    assert baseline.deployment_attempts == 1
    assert baseline.deployment_generation != previous_generation
    assert baseline.weights_path == "/weights/preserved"
    spawn.assert_not_called()


def test_api_and_mcp_share_progress_without_remote_credentials(deployed):
    from overbae.api.serializers import DeployedModelSerializer
    from overbae.services.mcp import resources

    warming(deployed)
    rest = DeployedModelSerializer(deployed).data["deployment_progress"]
    mcp = resources._deployment_resource(deployed.project, str(deployed.pk), "test")["progress"]
    assert rest == mcp
    assert rest["label"] == "Booting and verifying"
    assert "fc-warm" not in str(rest)


def test_new_recovery_schedule_replaces_janitor():
    from django.conf import settings

    assert settings.CELERY_BEAT_SCHEDULE["reconcile-deployments"]["schedule"] == 15.0
    assert "janitor-stuck-fsm" not in settings.CELERY_BEAT_SCHEDULE
    assert deployment.CLAIM_SECONDS + deployment.POLL_SECONDS <= 60


def test_superseded_notification_cannot_finish_new_attempt(deployed):
    DeployedModel.objects.filter(pk=deployed.pk).update(status="failed", deployment_notify=True)
    claimed = deployment._claim(deployed.pk)
    deployment.retry_deployment(deployed.pk)
    with patch("overbae.services.finetuning_eval.tick_job_evals") as tick:
        deployment._notify(claimed)
    tick.assert_not_called()
    deployed.finetuning_job.refresh_from_db()
    assert deployed.finetuning_job.status == "deploying"


def test_invalid_remote_result_never_leaks_provider_data(deployed):
    DeployedModel.objects.filter(pk=deployed.pk).update(
        deployment_stage="weights",
        deployment_call_id="fc-weights",
    )
    with patch.object(
        deployment, "poll_operation", return_value=("complete", "secret-provider-token")
    ):
        advance(deployed)
    assert "invalid result" in deployed.error_message
    assert "secret-provider-token" not in deployed.error_message


def test_failed_parent_waits_for_child_gpu_work_before_retry():
    child = SimpleNamespace(function_call_id="fc-gpu", status=InputStatus.PENDING, children=[])
    root = SimpleNamespace(
        function_call_id="fc-parent", status=InputStatus.TIMEOUT, children=[child]
    )
    with patch("modal.FunctionCall.from_id") as call:
        call.return_value.get.aio = AsyncMock(side_effect=TimeoutError("remote timeout"))
        call.return_value.get_call_graph.aio = AsyncMock(return_value=[root])
        assert deployment.poll_operation("fc-parent") == ("pending", None)
        child.status = InputStatus.TERMINATED
        assert deployment.poll_operation("fc-parent") == ("failed", "Remote operation timed out.")


def test_cancellation_covers_child_calls_without_terminating_shared_containers():
    child = SimpleNamespace(function_call_id="fc-gpu", status=InputStatus.PENDING, children=[])
    root = SimpleNamespace(
        function_call_id="fc-parent", status=InputStatus.TERMINATED, children=[child]
    )
    with patch("modal.FunctionCall.from_id") as call:
        call.return_value.get_call_graph.aio = AsyncMock(return_value=[root])
        call.return_value.cancel.aio = AsyncMock()
        assert not deployment.cancel_operation("fc-parent")
        assert {args.args[0] for args in call.call_args_list} == {"fc-parent", "fc-gpu"}
        for invocation in call.return_value.cancel.aio.call_args_list:
            assert invocation.kwargs == {}
