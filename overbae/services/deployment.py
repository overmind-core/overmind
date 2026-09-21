from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from datetime import timedelta

import modal
from asgiref.sync import async_to_sync
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from modal.call_graph import InputStatus

from overbae.modal.gpu_selector import select_gpu
from overbae.modal.model_registry import get_hf_base, get_model_config_any_backend
from overbae.models import DeployedModel, FinetuningJob, FinetuningJobEval
from overbae.services.finetuning_runner import MAX_ACTIVITY_LINES
from overbae.services.model_catalog import resolve_training_openrouter_slug
from overbae.services.plan_limits import PlanLimitExceeded, require_plan_quota

logger = logging.getLogger(__name__)

POLL_SECONDS = 15
CLAIM_SECONDS = 45
MAX_ATTEMPTS = 3
ACTIVE_STATUSES = ("queued", "quantizing", "deploying", "warming")
STAGE_LABELS = {
    "base": "Preparing base weights",
    "archive": "Archiving checkpoint",
    "weights": "Preparing checkpoint",
    "register": "Registering model",
    "warm": "Booting and verifying",
    "ready": "Ready",
}


def modal_environment() -> str | None:
    return os.environ.get("MODAL_ENVIRONMENT") or None


def base_model_slug(hf_base: str) -> str:
    return "base--" + hf_base.replace("/", "--").replace(".", "-").replace("_", "-").lower()[:200]


def model_slug(job: FinetuningJob) -> str:
    base = job.base_model.split("/")[-1].lower().replace(".", "-").replace("_", "-")
    return f"ft-{str(job.id)[:8]}-{base}"


def serves_as_adapter(job: FinetuningJob) -> bool:
    training_type = (job.hyperparameters or {}).get("training_type")
    kind = training_type.get("type") if isinstance(training_type, dict) else "Lora"
    if job.provider != "modal" or (kind or "Lora") != "Lora":
        return False
    config = get_model_config_any_backend(job.base_model) or {}
    return (config.get("inference") or {}).get("lora_supported", not config.get("moe", False))


def deployment_progress(deployed: DeployedModel) -> dict:
    return {
        "stage": deployed.deployment_stage,
        "label": STAGE_LABELS.get(deployed.deployment_stage, deployed.status.title()),
        "attempt": deployed.deployment_attempts,
        "retry_at": (
            deployed.deployment_next_poll_at.isoformat()
            if deployed.error_message
            and deployed.status in ACTIVE_STATUSES
            and not deployed.deployment_call_id
            and deployed.deployment_next_poll_at
            else None
        ),
        "deadline": deployed.deployment_deadline.isoformat()
        if deployed.deployment_deadline
        else None,
        "last_error": deployed.error_message or None,
    }


def _reset(deployed: DeployedModel) -> None:
    now = timezone.now()
    deployed.status = DeployedModel.Status.QUEUED
    deployed.status_changed_at = now
    deployed.deployment_generation = uuid.uuid4()
    deployed.deployment_stage = "base"
    deployed.deployment_attempts = 1
    deployed.deployment_call_id = ""
    deployed.deployment_dispatching = False
    deployed.deployment_deadline = now + timedelta(hours=4)
    deployed.deployment_next_poll_at = now
    deployed.deployment_claim = None
    deployed.deployment_claim_until = None
    deployed.deployment_notify = False
    deployed.error_message = ""
    deployed.save()


def ensure_training_deployment(job_id: str) -> DeployedModel | None:
    with transaction.atomic():
        job = (
            FinetuningJob.objects.select_for_update(of=("self",))
            .select_related("project", "triggered_by")
            .filter(pk=job_id)
            .first()
        )
        if not job or job.status not in ("succeeded", "deploying") or not job.remote_job_id:
            return None
        deployed = DeployedModel.objects.filter(finetuning_job=job).first()
        if deployed:
            if job.status == "deploying" and deployed.status in ("ready", "failed"):
                DeployedModel.objects.filter(pk=deployed.pk).update(
                    deployment_notify=True,
                    deployment_next_poll_at=timezone.now(),
                )
            return deployed
        if job.triggered_by:
            try:
                require_plan_quota(job.triggered_by, "deploy_jobs")
            except PlanLimitExceeded:
                FinetuningJob.objects.filter(pk=job.pk).update(
                    status="succeeded",
                    completed_at=timezone.now(),
                    error_message="Deploy skipped: Free plan deploy limit reached.",
                )
                return None
        cfg = get_model_config_any_backend(job.base_model) or {}
        context = int(
            (job.hyperparameters or {}).get("context_length")
            or (cfg.get("inference") or {}).get("max_model_len")
            or 8192
        )
        adapter = serves_as_adapter(job)
        gpu, _ = select_gpu({**cfg, "fp8_supported": False} if adapter else cfg, context)
        deployed = DeployedModel.objects.create(
            finetuning_job=job,
            project=job.project,
            model_id=model_slug(job),
            base_model_id=job.base_model,
            gpu_type=gpu,
            max_model_len=context,
            is_lora=adapter,
        )
        _reset(deployed)
        FinetuningJob.objects.filter(pk=job.pk).update(status="deploying", error_message="")
        return deployed


def ensure_baseline_deployment(job_id: str) -> DeployedModel | None:
    # Eval orchestration calls back into deployment initialization.
    from overbae.services.finetuning_eval import (
        baseline_needs_base_deploy,
        job_wants_evals,
        tick_job_evals,
    )

    job = FinetuningJob.objects.select_related("project", "capability").filter(pk=job_id).first()
    if not job or job.status in ("failed", "cancelled") or not job_wants_evals(job):
        return None
    if not baseline_needs_base_deploy(job):
        tick_job_evals(job)
        return None
    cfg = get_model_config_any_backend(job.base_model) or {}
    context = int(
        (job.hyperparameters or {}).get("context_length")
        or (cfg.get("inference") or {}).get("max_model_len")
        or 8192
    )
    gpu, _ = select_gpu(cfg, context)
    with transaction.atomic():
        deployed, created = DeployedModel.objects.get_or_create(
            model_id=base_model_slug(get_hf_base(job.base_model)),
            defaults={
                "project": job.project,
                "base_model_id": job.base_model,
                "gpu_type": gpu,
                "max_model_len": context,
            },
        )
        deployed = DeployedModel.objects.select_for_update().get(pk=deployed.pk)
        new_waiter = not deployed.deployment_waiters.filter(pk=job.pk).exists()
        deployed.deployment_waiters.add(job)
        if not created and not deployed.deployment_stage and deployed.status == "failed":
            deployed.deployment_dispatching = True
            deployed.error_message = "Deployment has no saved remote operation. Confirm the previous operation has stopped before retrying."
            deployed.save(update_fields=["deployment_dispatching", "error_message"])
        if created or (
            new_waiter
            and deployed.status == "failed"
            and not deployed.deployment_dispatching
            and not deployed.deployment_cancel_pending
        ):
            _reset(deployed)
        elif deployed.status in ("ready", "failed"):
            deployed.deployment_notify = True
            deployed.deployment_next_poll_at = timezone.now()
            deployed.save(update_fields=["deployment_notify", "deployment_next_poll_at"])
    _publish_progress(deployed)
    return deployed


def retry_baseline_deployments(job: FinetuningJob) -> None:
    if resolve_training_openrouter_slug(job.base_model):
        job.evaluation_deployments.clear()
        return
    with transaction.atomic():
        rows = list(job.evaluation_deployments.select_for_update().filter(status="failed"))
        if any(row.deployment_dispatching or row.deployment_cancel_pending for row in rows):
            raise ValueError(
                "The previous evaluation deployment is unresolved; confirm it has stopped before retrying."
            )
        for row in rows:
            _reset(row)


def retry_deployment(deployment_id) -> DeployedModel:
    with transaction.atomic():
        job_id = DeployedModel.objects.values_list("finetuning_job_id", flat=True).get(
            pk=deployment_id
        )
        if job_id:
            FinetuningJob.objects.select_for_update().get(pk=job_id)
        deployed = (
            DeployedModel.objects.select_for_update(of=("self",))
            .select_related("finetuning_job")
            .get(pk=deployment_id)
        )
        if deployed.status not in ("failed", "deleted"):
            raise ValueError("Only failed or deleted deployments can be retried.")
        if deployed.deployment_cancel_pending or deployed.deployment_dispatching:
            raise ValueError(
                "The previous remote operation is unresolved; confirm it has stopped before retrying."
            )
        job = deployed.finetuning_job
        if not job or job.status not in ("succeeded", "deploying") or not job.remote_job_id:
            raise ValueError(
                "Training job has no usable checkpoint — retry the training job first."
            )
        _reset(deployed)
        FinetuningJobEval.objects.filter(
            job=job,
            kind="final",
            status="failed",
            eval_run__isnull=True,
            error_message__startswith="Deployment:",
        ).delete()
        FinetuningJob.objects.filter(pk=job.pk).update(status="deploying", error_message="")
        return deployed


def _remote_operation(deployed: DeployedModel):
    env = modal_environment()
    base = get_hf_base(deployed.base_model_id)
    job = deployed.finetuning_job
    cfg = get_model_config_any_backend(deployed.base_model_id) or {}
    stage = deployed.deployment_stage
    if stage == "base":
        return modal.Function.from_name(
            "overmind-register", "fetch_base_model", environment_name=env
        ), {"base_model": base}
    if stage == "archive":
        return modal.Function.from_name(
            "overmind-register", "sync_modal_checkpoint_to_s3", environment_name=env
        ), {
            "remote_id": job.remote_job_id,
            "user_id": str(job.triggered_by_id) if job.triggered_by_id else "",
            "job_id": str(job.id),
            "job_result": job.result if isinstance(job.result, dict) else {},
        }
    if stage == "weights":
        if job and serves_as_adapter(job):
            return modal.Function.from_name(
                "overmind-register", "publish_adapter", environment_name=env
            ), {
                "remote_id": job.remote_job_id,
                "cache_key": deployed.model_id,
                "base_model": base,
                "user_id": str(job.triggered_by_id) if job.triggered_by_id else "",
                "job_id": str(job.id),
            }
        server = modal.Cls.from_name(
            "overmind-register", "RegisterAPIServer", environment_name=env
        )()
        if not job:
            return server.register_base, {
                "base_model": base,
                "model_id": deployed.model_id,
                "quantize": cfg.get("fp8_supported", True),
                "params_b": float(cfg.get("total_params_b") or 0),
            }
        return server.register, {
            "remote_job_id": job.remote_job_id,
            "model_id": deployed.model_id,
            "provider": job.provider,
            "baseten_api_key": getattr(settings, "BASETEN_API_KEY", "") or "",
            "user_id": str(job.triggered_by_id) if job.triggered_by_id else "",
            "job_id": str(job.id),
            "job_result": job.result if isinstance(job.result, dict) else {},
            "quantize": cfg.get("fp8_supported", True),
            "merge_base_model": base,
            "params_b": float(cfg.get("total_params_b") or 0),
        }
    if stage == "register":
        return modal.Cls.from_name(
            "overmind-inference", "InferenceAPIServer", environment_name=env
        )().register_model, {
            "model_id": deployed.model_id,
            "weights_path": deployed.weights_path,
            "max_model_len": deployed.max_model_len,
            "gpu_type": deployed.gpu_type,
            "tokenizer_name": base,
        }
    if stage == "warm":
        return modal.Function.from_name("overmind-inference", "pre_warm", environment_name=env), {
            "model_id": deployed.model_id,
            "weights_path": deployed.weights_path,
            "gpu_type": deployed.gpu_type,
            "max_model_len": deployed.max_model_len,
            "adapter": (deployed.model_id, deployed.adapter_path.removeprefix("/weights/"))
            if deployed.adapter_path
            else None,
            "lora_rank": deployed.lora_rank if deployed.adapter_path else 0,
        }
    raise ValueError("Unknown deployment stage")


def spawn_operation(deployed: DeployedModel) -> str:
    fn, kwargs = _remote_operation(deployed)
    return _bounded(fn.spawn.aio, **kwargs).object_id


def _bounded(method, *args, **kwargs):
    async def invoke():
        return await asyncio.wait_for(method(*args, **kwargs), timeout=10)

    return async_to_sync(invoke)()


def poll_operation(call_id: str) -> tuple[str, object]:
    call = modal.FunctionCall.from_id(call_id)
    try:
        return "complete", _bounded(call.get.aio, timeout=0)
    except Exception as exc:
        # A transport failure is not proof the remote operation failed. Reconnect to
        # this handle until the provider confirms a terminal result or the deadline.
        graph = _bounded(call.get_call_graph.aio)
        terminal = {
            InputStatus.FAILURE,
            InputStatus.INIT_FAILURE,
            InputStatus.TERMINATED,
            InputStatus.TIMEOUT,
        }
        if any(node.function_call_id == call_id and node.status in terminal for node in graph):
            if any(node.status == InputStatus.PENDING for node in _call_tree(graph)):
                return "pending", None
            logger.exception("Deployment remote operation %s failed", call_id)
            detail = str(exc).lower()
            if "manifest" in detail:
                reason = "Base-weight manifest missing or invalid."
            elif "out of memory" in detail:
                reason = "GPU ran out of memory."
            elif "timeout" in detail or "timed out" in detail:
                reason = "Remote operation timed out."
            else:
                reason = "Remote operation failed."
            return "failed", reason
        if isinstance(exc, TimeoutError):
            return "pending", None
        raise


def _call_tree(nodes):
    for node in nodes:
        yield node
        yield from _call_tree(node.children)


def cancel_operation(call_id: str) -> bool:
    call = modal.FunctionCall.from_id(call_id)
    try:
        graph = _bounded(call.get_call_graph.aio)
        call_ids = {call_id} | {
            node.function_call_id
            for node in _call_tree(graph)
            if node.status == InputStatus.PENDING
        }

        async def cancel_one(handle):
            with contextlib.suppress(modal.exception.NotFoundError):
                await modal.FunctionCall.from_id(handle).cancel.aio()

        async def cancel_calls():
            await asyncio.gather(*(cancel_one(handle) for handle in call_ids))

        _bounded(cancel_calls)
        graph = _bounded(call.get_call_graph.aio)
    except modal.exception.NotFoundError:
        return True
    return bool(graph) and all(node.status != InputStatus.PENDING for node in _call_tree(graph))


def _claim(deployment_id) -> DeployedModel | None:
    now = timezone.now()
    with transaction.atomic():
        deployed = (
            DeployedModel.objects.select_for_update(skip_locked=True, of=("self",))
            .select_related("finetuning_job")
            .filter(pk=deployment_id)
            .first()
        )
        if not deployed or (
            deployed.deployment_claim_until and deployed.deployment_claim_until > now
        ):
            return None
        if deployed.status not in ACTIVE_STATUSES and not (
            deployed.deployment_cancel_pending or deployed.deployment_notify
        ):
            return None
        if deployed.deployment_next_poll_at and deployed.deployment_next_poll_at > now:
            return None
        deployed.deployment_claim = uuid.uuid4()
        deployed.deployment_claim_until = now + timedelta(seconds=CLAIM_SECONDS)
        deployed.save(update_fields=["deployment_claim", "deployment_claim_until"])
        return deployed


def _owned(deployed: DeployedModel):
    return DeployedModel.objects.filter(
        pk=deployed.pk,
        deployment_generation=deployed.deployment_generation,
        deployment_claim=deployed.deployment_claim,
        status=deployed.status,
    )


def _save(deployed: DeployedModel, **values) -> bool:
    updated = bool(_owned(deployed).update(**values))
    if updated:
        for key, value in values.items():
            setattr(deployed, key, value)
    return updated


def _finish(deployed: DeployedModel, *, error: str = "") -> None:
    now = timezone.now()
    with transaction.atomic():
        if deployed.finetuning_job_id:
            job = FinetuningJob.objects.select_for_update().get(pk=deployed.finetuning_job_id)
            if job.status in ("failed", "cancelled"):
                error = "Deployment stopped: training job is " + job.status + "."
        _save(
            deployed,
            status="failed" if error else "ready",
            status_changed_at=now,
            error_message=error,
            deployment_dispatching=deployed.deployment_dispatching or not deployed.deployment_stage,
            deployment_notify=True,
            deployment_cancel_pending=bool(error and deployed.deployment_call_id),
            deployed_at=deployed.deployed_at if error else now,
            deployment_stage=deployed.deployment_stage if error else "ready",
        )


def _publish_progress(deployed: DeployedModel) -> None:
    job_ids = (
        [deployed.finetuning_job_id]
        if deployed.finetuning_job_id
        else list(deployed.deployment_waiters.values_list("pk", flat=True))
    )
    key = "deployment" if deployed.finetuning_job_id else "baseline_deployment"
    for job_id in job_ids:
        with transaction.atomic():
            job = FinetuningJob.objects.select_for_update().get(pk=job_id)
            if job.status in ("failed", "cancelled"):
                continue
            deployed.refresh_from_db()
            progress = dict(job.progress or {})
            state = deployment_progress(deployed)
            if progress.get(key) == state:
                continue
            progress[key] = state
            message = state["label"]
            if state["retry_at"]:
                seconds = max(
                    0, round((deployed.deployment_next_poll_at - timezone.now()).total_seconds())
                )
                message = f"{message} — retrying in {seconds}s"
            elif deployed.status == "failed":
                message = deployed.error_message
            if not deployed.finetuning_job_id:
                message = "Baseline evaluation: " + message
            activity = list(progress.get("activity") or [])
            activity.append({"ts": int(timezone.now().timestamp() * 1000), "message": message})
            progress["activity"] = activity[-MAX_ACTIVITY_LINES:]
            FinetuningJob.objects.filter(pk=job.pk).update(progress=progress)


def _notify(deployed: DeployedModel) -> None:
    # Eval scheduling resolves deployments, so importing it at module scope cycles.
    from overbae.services.finetuning_eval import resolve_baseline_model, tick_job_evals

    with transaction.atomic():
        if deployed.finetuning_job_id:
            FinetuningJob.objects.select_for_update().get(pk=deployed.finetuning_job_id)
        current = DeployedModel.objects.select_for_update().get(pk=deployed.pk)
        if (
            current.deployment_generation != deployed.deployment_generation
            or current.deployment_claim != deployed.deployment_claim
            or current.status != deployed.status
        ):
            return
        job_ids = list(deployed.deployment_waiters.values_list("pk", flat=True))
        if deployed.finetuning_job_id:
            job_ids.append(deployed.finetuning_job_id)
            FinetuningJob.objects.filter(pk=deployed.finetuning_job_id, status="deploying").update(
                status="succeeded",
                completed_at=timezone.now(),
                error_message=deployed.error_message,
            )
        for job in (
            FinetuningJob.objects.select_related("capability__benchmark_model")
            .filter(pk__in=job_ids)
            .exclude(status__in=("failed", "cancelled"))
        ):
            if deployed.status == "failed" and job.eval_set_id and job.eval_dataset_id:
                kinds = []
                if deployed.finetuning_job_id and job.eval_model_after:
                    kinds.append("final")
                elif not deployed.finetuning_job_id:
                    if job.eval_model_before:
                        kinds.append("model_before")
                    if job.eval_incumbent_before and not resolve_baseline_model(job):
                        kinds.append("baseline")
                for kind in kinds:
                    FinetuningJobEval.objects.get_or_create(
                        job=job,
                        kind=kind,
                        defaults={
                            "status": "failed",
                            "model_id": deployed.model_id,
                            "error_message": "Deployment: " + deployed.error_message,
                        },
                    )
            tick_job_evals(job)
    _save(deployed, deployment_notify=False)


def _complete_stage(deployed: DeployedModel, result) -> None:
    values = {"deployment_call_id": "", "deployment_dispatching": False, "error_message": ""}
    stage = deployed.deployment_stage
    if stage == "base":
        values["deployment_stage"] = (
            "archive"
            if deployed.finetuning_job and deployed.finetuning_job.provider == "modal"
            else "weights"
        )
    elif stage == "archive":
        values["deployment_stage"] = "weights"
    elif stage == "weights":
        if deployed.finetuning_job and serves_as_adapter(deployed.finetuning_job):
            values.update(
                weights_path="/weights/" + result["base_path"].removeprefix("/weights/"),
                adapter_path="/weights/.adapters/" + deployed.model_id,
                lora_rank=int(result.get("lora_rank") or 16),
                is_lora=True,
                quantization="bf16",
            )
        else:
            values.update(
                weights_path=result["weights_path"],
                adapter_path="",
                is_lora=False,
                quantization="bf16"
                if result.get("quantization") in ("none", "bf16", "")
                else "fp8",
                num_parameters=result.get("num_parameters", 0),
            )
        values["deployment_stage"] = "register"
    elif stage == "register":
        if not isinstance(result, str) or not result:
            raise ValueError("Registration returned no serving endpoint")
        values.update(
            inference_url=result,
            deployment_stage="warm",
            status="warming",
            status_changed_at=timezone.now(),
        )
    elif stage == "warm":
        # pre_warm completes only after a successful inference request, including the adapter.
        if _save(deployed, **values):
            deployed.deployment_call_id = ""
            _finish(deployed)
        return
    _save(deployed, **values)


def advance_deployment(deployment_id) -> None:
    deployed = _claim(deployment_id)
    if not deployed:
        return
    next_poll = timezone.now() + timedelta(seconds=POLL_SECONDS)
    try:
        # Surface failure to dependants even while remote cancellation is unresolved.
        if deployed.deployment_notify:
            _notify(deployed)
            if not deployed.deployment_cancel_pending:
                return
        if deployed.deployment_cancel_pending:
            if cancel_operation(deployed.deployment_call_id):
                _save(
                    deployed,
                    deployment_cancel_pending=False,
                    deployment_call_id="",
                    deployment_dispatching=False,
                )
            return
        job = deployed.finetuning_job
        if job and job.status in ("cancelled", "failed"):
            _finish(deployed, error="Deployment stopped: training job is " + job.status + ".")
            return
        if (
            not job
            and not deployed.deployment_waiters.exclude(status__in=("failed", "cancelled")).exists()
        ):
            _finish(deployed, error="Deployment stopped: no evaluation is waiting for this model.")
            return
        if not deployed.deployment_stage:
            # Existing long-running calls have no recoverable handle. Never duplicate them.
            _save(deployed, deployment_dispatching=True)
            _finish(
                deployed,
                error="Deployment has no saved remote operation. Confirm the previous operation has stopped before retrying.",
            )
            return
        if deployed.deployment_deadline and timezone.now() >= deployed.deployment_deadline:
            _finish(deployed, error="Deployment deadline exceeded. Checkpoint preserved.")
            return
        if deployed.deployment_call_id:
            state, result = poll_operation(deployed.deployment_call_id)
            if state == "complete":
                try:
                    _complete_stage(deployed, result)
                except (KeyError, TypeError, ValueError):
                    logger.exception("Invalid result for deployment %s", deployed.pk)
                    state = "failed"
                    result = "Remote operation returned an invalid result."
            if state == "failed":
                message = f"{STAGE_LABELS[deployed.deployment_stage]} failed."
                if isinstance(result, str):
                    message += " " + result
                _save(deployed, deployment_call_id="", deployment_dispatching=False)
                deployed.deployment_call_id = ""
                if deployed.deployment_stage == "archive":
                    _save(
                        deployed,
                        deployment_stage="weights",
                        error_message="",
                    )
                elif deployed.deployment_attempts >= MAX_ATTEMPTS:
                    _finish(deployed, error=message + " Retry limit reached. Checkpoint preserved.")
                else:
                    next_poll = timezone.now() + timedelta(
                        seconds=30 * 2 ** (deployed.deployment_attempts - 1)
                    )
                    # Revalidate the canonical base before retrying a failed boot.
                    _save(
                        deployed,
                        deployment_attempts=deployed.deployment_attempts + 1,
                        deployment_stage="base"
                        if deployed.deployment_stage == "warm"
                        else deployed.deployment_stage,
                        error_message=message,
                        deployment_next_poll_at=next_poll,
                    )
            return
        if deployed.deployment_dispatching:
            _finish(
                deployed,
                error="Remote submission acknowledgement lost. No duplicate operation was launched; confirm remote state before retrying.",
            )
            return
        if not _save(
            deployed,
            deployment_dispatching=True,
            status="warming" if deployed.deployment_stage == "warm" else "deploying",
            status_changed_at=timezone.now(),
        ):
            return
        try:
            call_id = spawn_operation(deployed)
        except Exception:
            logger.exception("Deployment submission unresolved for %s", deployed.pk)
            _finish(
                deployed,
                error="Remote submission could not be confirmed. Confirm remote state before retrying.",
            )
            return
        # Even a late acknowledgement can be attached after a claim expires, as long
        # as this generation still owns the submission. A retry cannot erase ambiguity.
        DeployedModel.objects.filter(
            pk=deployed.pk,
            deployment_generation=deployed.deployment_generation,
            deployment_dispatching=True,
            deployment_call_id="",
        ).update(
            deployment_call_id=call_id,
            deployment_dispatching=False,
            deployment_cancel_pending=Q(status__in=("failed", "deleting", "deleted")),
        )
    except Exception:
        logger.exception(
            "Deployment observation failed for %s; retaining remote handle", deployed.pk
        )
    finally:
        if _save(
            deployed,
            deployment_next_poll_at=next_poll,
            deployment_claim=None,
            deployment_claim_until=None,
        ):
            deployed.refresh_from_db()
            _publish_progress(deployed)


def due_deployments():
    now = timezone.now()
    return (
        DeployedModel.objects.filter(
            Q(status__in=ACTIVE_STATUSES)
            | Q(deployment_cancel_pending=True)
            | Q(deployment_notify=True),
        )
        .filter(Q(deployment_next_poll_at__isnull=True) | Q(deployment_next_poll_at__lte=now))
        .filter(Q(deployment_claim_until__isnull=True) | Q(deployment_claim_until__lte=now))
    )
