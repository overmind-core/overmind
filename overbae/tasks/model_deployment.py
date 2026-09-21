"""Registers a fine-tuned model with the vLLM inference platform once training succeeds.

Retries resume off ``DeployedModel.status``: WARMING skips download + register and re-runs only
pre_warm, DEPLOYING/FAILED/QUEUED restart from download, READY just ensures the FinetuningJob is
SUCCEEDED. ``weights_path`` and ``inference_url`` are persisted before pre_warm so that resume
costs no re-download.

pre_warm boots the deployment once to prove it serves before it is marked READY, and leaves the
container up so the first real request does not pay a cold start.
"""

from __future__ import annotations

import contextlib
import logging
import os
import sys
from collections.abc import Sequence

from celery import shared_task
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from overbae.modal.gpu_selector import select_gpu
from overbae.modal.model_registry import get_hf_base, get_model_config_any_backend

logger = logging.getLogger(__name__)


_MODAL_APP_NAME = "overmind-inference"
_MODAL_REGISTER_APP = "overmind-register"
_API_SERVER_CLS = "InferenceAPIServer"


def _modal_env() -> str | None:
    return os.environ.get("MODAL_ENVIRONMENT") or None


def _make_model_id(job) -> str:
    base = job.base_model.split("/")[-1].lower().replace(".", "-").replace("_", "-")
    return f"ft-{str(job.id)[:8]}-{base}"


def record_deploy_stages(job_pk, messages: Sequence[str]) -> None:
    """Appends to ``job.progress["activity"]`` in one write, same {ts, message} shape and cap as
    the Baseten poll-loop lines. Batched on purpose: ``capture_modal_logs`` flushes a container's
    worth of lines at once, and one row-locking transaction per line would rewrite the whole
    progress JSON dozens of times."""
    import time

    from overbae.models import FinetuningJob
    from overbae.services.finetuning_runner import MAX_ACTIVITY_LINES, scrub_backend_names

    if not messages:
        return
    try:
        now_ms = int(time.time() * 1000)
        with transaction.atomic():
            job = FinetuningJob.objects.select_for_update().get(pk=job_pk)
            progress = dict(job.progress) if isinstance(job.progress, dict) else {}
            activity = list(progress.get("activity") or [])
            activity.extend(
                {"ts": now_ms, "message": scrub_backend_names(m)[:300]} for m in messages
            )
            progress["activity"] = activity[-MAX_ACTIVITY_LINES:]
            FinetuningJob.objects.filter(pk=job_pk).update(progress=progress)
    except Exception:  # noqa: BLE001 — activity lines must never fail deployment
        logger.warning("failed to record deploy stages for job %s", job_pk, exc_info=True)


def record_deploy_stage(job_pk, message: str) -> None:
    record_deploy_stages(job_pk, [message])


class _Tee:
    def __init__(self, real, buf):
        self._real = real
        self._buf = buf

    def write(self, s):
        self._buf.write(s)
        return self._real.write(s)

    def flush(self):
        self._real.flush()


@contextlib.contextmanager
def capture_modal_logs(job_pk):
    """``modal.enable_output()`` streams the remote container's stdout/stderr into this process;
    the tee'd lines are sanitized and appended to the deploy activity feed. Best-effort: capture
    never blocks or fails the deploy.
    """
    import io

    from overbae.services.finetuning_runner import sanitize_modal_log_lines

    buf = io.StringIO()
    stack = contextlib.ExitStack()
    # Setup is isolated from the yield: a missing or failing enable_output must
    # never break the deploy call it wraps.
    try:
        import modal

        enable_output = getattr(modal, "enable_output", None)
        if enable_output is not None:
            stack.enter_context(contextlib.redirect_stdout(_Tee(sys.stdout, buf)))
            stack.enter_context(contextlib.redirect_stderr(_Tee(sys.stderr, buf)))
            stack.enter_context(enable_output())
    except Exception:  # noqa: BLE001 — capture is optional; deploy proceeds regardless
        logger.warning("modal log capture setup failed for job %s", job_pk, exc_info=True)
    try:
        yield
    finally:
        stack.close()
        try:
            record_deploy_stages(job_pk, sanitize_modal_log_lines(buf.getvalue().splitlines()))
        except Exception:  # noqa: BLE001 — log capture must never fail deployment
            logger.warning("modal log capture failed for job %s", job_pk, exc_info=True)


def _retry_or_fail_deploy(task, job, exc):
    """Terminal-fails the job once retries are exhausted. Otherwise it sits in DEPLOYING forever
    while the 60 s reconciler re-enqueues the deploy against a permanently failing target."""
    from celery.exceptions import MaxRetriesExceededError

    from overbae.models import FinetuningJob

    try:
        raise task.retry(exc=exc)
    except MaxRetriesExceededError:
        logger.error("Deploy exhausted retries for job %s: %s", job.pk, exc)
        msg = "Deployment failed"
        record_deploy_stage(job.pk, msg)
        FinetuningJob.objects.filter(pk=job.pk, status=FinetuningJob.Status.DEPLOYING).update(
            status=FinetuningJob.Status.FAILED,
            error_message=msg,
            completed_at=timezone.now(),
        )
        raise


@shared_task(bind=True, max_retries=3, default_retry_delay=120)
def register_finetuned_model(self, *, job_id: str) -> None:
    """Per-job Redis lock: pre_warm blocks for minutes (3-5 for Mamba/hybrid Triton JIT) and
    redelivery — acks_late worker restart, or the reconciler racing a live task — can dispatch a
    second copy. Concurrent pre_warm calls make Modal cancel one mid-flight; the lock turns the
    duplicate into a no-op.

    The lock timeout must outlive the slowest deploy or it defeats itself: a 72B measures ~58 min
    end to end (fetch + merge + FP8 + pre_warm).
    """
    from overbae.tasks.utils.task_lock import acquire_task_lock

    with acquire_task_lock(f"register_finetuned_model:{job_id}", timeout=3 * 60 * 60) as acquired:
        if not acquired:
            logger.info(
                "register_finetuned_model already running for job %s — skipping duplicate.",
                job_id,
            )
            return
        _register_finetuned_model(self, job_id=job_id)


def _register_finetuned_model(self, *, job_id: str) -> None:
    import modal

    from overbae.models import DeployedModel, FinetuningJob

    try:
        job = FinetuningJob.objects.select_related("project", "triggered_by").get(id=job_id)
    except FinetuningJob.DoesNotExist:
        logger.error("FinetuningJob %s not found.", job_id)
        return

    if job.status not in (FinetuningJob.Status.SUCCEEDED, FinetuningJob.Status.DEPLOYING):
        logger.info(
            "FinetuningJob %s status=%s — skipping registration.",
            job_id,
            job.status,
        )
        return

    if not job.remote_job_id:
        logger.warning("FinetuningJob %s has no remote job ID.", job_id)
        return

    # Free deploy cap: only when inserting a new DeployedModel for this job.
    if not DeployedModel.objects.filter(finetuning_job=job).exists():
        from overbae.services.plan_limits import PlanLimitExceeded, require_plan_quota

        owner = job.triggered_by
        if owner is not None:
            try:
                require_plan_quota(owner, "deploy_jobs")
            except PlanLimitExceeded:
                msg = (
                    "Deploy skipped: Free plan deploy limit reached. "
                    "Upgrade to Pro for unlimited deploys."
                )
                logger.info("FinetuningJob %s — %s", job_id, msg)
                FinetuningJob.objects.filter(pk=job.pk).update(
                    status=FinetuningJob.Status.SUCCEEDED,
                    error_message=msg,
                    completed_at=timezone.now(),
                )
                return

    base_model_id = job.base_model
    # The training context_length (persisted by BasetenRunner.submit) keeps the
    # inference worker able to take prompts as long as training saw, and lets
    # gpu_selector pick the cheapest GPU that fits.
    training_ctx = int((job.hyperparameters or {}).get("context_length") or 0)
    model_cfg = get_model_config_any_backend(base_model_id) or {}
    static_max_model_len = (model_cfg.get("inference") or {}).get("max_model_len") or 8192
    max_model_len = training_ctx if training_ctx > 0 else static_max_model_len
    serve_adapter = _serves_as_adapter(job)
    # An adapter rides an unquantized base, so the tier has to hold BF16 weights even for a
    # model whose merged checkpoint would have been FP8.
    gpu_type, _max_concurrent = select_gpu(
        {**model_cfg, "fp8_supported": False} if serve_adapter else model_cfg,
        max_model_len,
    )
    model_id = _make_model_id(job)
    env = _modal_env()

    resume_pre_warm = False
    with transaction.atomic():
        deployed, created = DeployedModel.objects.get_or_create(
            finetuning_job=job,
            defaults={
                "project": job.project,
                "model_id": model_id,
                "base_model_id": base_model_id,
                "gpu_type": gpu_type,
                "max_model_len": max_model_len,
                "status": DeployedModel.Status.QUEUED,
            },
        )

        if not created:
            if deployed.status == DeployedModel.Status.READY:
                logger.info(
                    "Model %s already READY — ensuring job is SUCCEEDED.", deployed.model_id
                )
                FinetuningJob.objects.filter(
                    pk=job.pk, status=FinetuningJob.Status.DEPLOYING
                ).update(status=FinetuningJob.Status.SUCCEEDED, completed_at=timezone.now())
                return

            if (
                deployed.status == DeployedModel.Status.WARMING
                and deployed.weights_path
                and deployed.inference_url
            ):
                # Flag only, never run pre_warm inside this transaction: it blocks
                # for minutes and its READY tick enqueues eval tasks, which would
                # see an uncommitted EvalRun and exit "not found".
                resume_pre_warm = True
            else:
                deployed.status = DeployedModel.Status.QUEUED
                deployed.error_message = ""
                deployed.save(update_fields=["status", "error_message"])

    if resume_pre_warm:
        logger.info(
            "Model %s resuming from WARMING — skipping download/register, re-running pre_warm.",
            deployed.model_id,
        )
        _run_pre_warm(
            self,
            deployed,
            job,
            env,
            weights_path=deployed.weights_path,
            gpu_type=gpu_type,
            max_model_len=max_model_len,
        )
        return

    DeployedModel.objects.filter(pk=deployed.pk).update(
        status=DeployedModel.Status.DEPLOYING, status_changed_at=timezone.now()
    )
    record_deploy_stage(
        job.pk,
        f"Fetching checkpoint and preparing weights ({deployed.model_id})…",
    )

    _archive_checkpoint_for_download(job, env)

    if serve_adapter:
        _deploy_as_adapter(
            self,
            deployed,
            job,
            env,
            base_model_id=get_hf_base(base_model_id),
            gpu_type=gpu_type,
            max_model_len=max_model_len,
        )
        return

    try:
        register_api_cls = modal.Cls.from_name(
            _MODAL_REGISTER_APP, "RegisterAPIServer", environment_name=env
        )
        register_api = register_api_cls()
        with capture_modal_logs(job.pk):
            result = register_api.register.remote(
                remote_job_id=job.remote_job_id,
                model_id=deployed.model_id,
                provider=job.provider,
                baseten_api_key=getattr(settings, "BASETEN_API_KEY", "") or "",
                # baseten/modal only: addresses the S3 checkpoint archive at
                # s3://{bucket}/{user_id}/{job_id}/.
                user_id=str(job.triggered_by_id) if job.triggered_by_id else "",
                job_id=str(job.id),
                job_result=job.result if isinstance(job.result, dict) else {},
                # fp8_supported=false serves BF16 — some MoE experts do not survive
                # llmcompressor's FP8_DYNAMIC recipe (register_model.prepare_fp8).
                quantize=model_cfg.get("fp8_supported", True),
                # Unsloth QLoRA rewrites adapter_config to a bnb-4bit repo that cannot
                # be the merge base, so resolve the real HF id.
                merge_base_model=get_hf_base(base_model_id),
                # Only selects the merge/quantize GPU tier.
                params_b=float(model_cfg.get("total_params_b") or 0.0),
            )
    except Exception as exc:
        logger.error("Download failed for %s: %s", deployed.model_id, exc)
        record_deploy_stage(job.pk, "Checkpoint download failed — will retry")
        DeployedModel.objects.filter(pk=deployed.pk).update(
            status=DeployedModel.Status.FAILED,
            error_message="Download failed",
        )
        _retry_or_fail_deploy(self, job, exc)

    weights_path: str = result["weights_path"]
    # register_model.py writes "fp8" after merge+quantize; map legacy "none" → BF16.
    quantization = (
        DeployedModel.Quantization.BF16
        if result.get("quantization") in ("none", "bf16", "")
        else result.get("quantization", DeployedModel.Quantization.FP8)
    )
    is_lora: bool = result.get("is_lora", False)
    record_deploy_stage(
        job.pk,
        f"Weights ready ({'LoRA merged, ' if is_lora else ''}{quantization}) — "
        "registering with the inference server…",
    )

    try:
        api_server_cls = modal.Cls.from_name(_MODAL_APP_NAME, _API_SERVER_CLS, environment_name=env)
        api_server = api_server_cls()
        inference_url: str = api_server.register_model.remote(
            model_id=deployed.model_id,
            weights_path=weights_path,
            max_model_len=max_model_len,
            gpu_type=gpu_type,
            tokenizer_name=result.get("base_model", ""),
        )
    except Exception as exc:
        logger.error("InferenceAPIServer registration failed for %s: %s", deployed.model_id, exc)
        record_deploy_stage(job.pk, "Inference-server registration failed — will retry")
        DeployedModel.objects.filter(pk=deployed.pk).update(
            status=DeployedModel.Status.FAILED,
            error_message="Registration failed",
        )
        _retry_or_fail_deploy(self, job, exc)

    # Persisted before pre_warm so a retry after a pre_warm failure re-warms only.
    DeployedModel.objects.filter(pk=deployed.pk).update(
        weights_path=weights_path,
        quantization=quantization,
        num_parameters=result.get("num_parameters", 0),
        gpu_type=gpu_type,
        is_lora=is_lora,
        inference_url=inference_url,
        error_message="",
    )
    deployed.refresh_from_db()

    _run_pre_warm(
        self,
        deployed,
        job,
        env,
        weights_path=weights_path,
        gpu_type=gpu_type,
        max_model_len=max_model_len,
    )


def _archive_checkpoint_for_download(job, env: str) -> None:
    """Archive a Modal-trained checkpoint to S3, which is the only thing the user's
    "Download weights" button reads.

    Modal deploys stage weights off the sft Volume and never read this archive, so it belongs
    here rather than inside ``RegisterAPIServer.register`` — the adapter path returns before
    ``register`` and would otherwise leave a LoRA finetune with nothing to download. Baseten
    archives inside ``register`` instead, where the zip is a genuine prerequisite of the
    download-and-merge step.
    """
    if job.provider != "modal":
        return

    import modal

    try:
        modal.Function.from_name(
            _MODAL_REGISTER_APP, "sync_modal_checkpoint_to_s3", environment_name=env
        ).spawn(
            remote_id=job.remote_job_id,
            user_id=str(job.triggered_by_id) if job.triggered_by_id else "",
            job_id=str(job.id),
            job_result=job.result if isinstance(job.result, dict) else {},
        )
    except Exception as exc:
        # Download is a convenience surface; the deploy must not fail with it.
        logger.warning("Checkpoint archive spawn failed for job %s: %s", job.pk, exc)


def _serves_as_adapter(job) -> bool:
    """Whether this finetune should be served as a LoRA adapter on a shared base.

    Only Modal-trained LoRA qualifies: the adapter has to be readable off the overmind-sft
    Volume, which is where ``publish_adapter`` reads it from. Baseten and Nebius runs come back
    through S3 and still take the merge path.

    MoE adapter serving requires verified support for the training checkpoint's expert format.
    """
    training_type = (job.hyperparameters or {}).get("training_type")
    kind = (training_type or {}).get("type") if isinstance(training_type, dict) else None
    if job.provider != "modal" or str(kind or "Lora") != "Lora":
        return False
    config = get_model_config_any_backend(job.base_model) or {}
    return (config.get("inference") or {}).get("lora_supported", not config.get("moe", False))


def _deploy_as_adapter(
    self_task, deployed, job, env, *, base_model_id, gpu_type, max_model_len
) -> None:
    """Publish the adapter and point the deployment at the shared base it rides on.

    No merge and no quantize: the deployment is a few tens of MB of adapter against a full
    checkpoint copy, and it joins whatever container pool already serves that base.
    """
    import modal

    from overbae.models import DeployedModel

    try:
        publish = modal.Function.from_name(
            _MODAL_REGISTER_APP, "publish_adapter", environment_name=env
        )
        with capture_modal_logs(job.pk):
            meta = publish.remote(
                remote_id=job.remote_job_id,
                cache_key=deployed.model_id,
                base_model=base_model_id,
                # Addresses the S3 archive, which is the only source left once retention has
                # pruned the training checkpoint and the deployment was deleted.
                user_id=str(job.triggered_by_id) if job.triggered_by_id else "",
                job_id=str(job.id),
            )
    except Exception as exc:
        logger.error("Adapter publish failed for %s: %s", deployed.model_id, exc)
        record_deploy_stage(job.pk, "Adapter publish failed — will retry")
        DeployedModel.objects.filter(pk=deployed.pk).update(
            status=DeployedModel.Status.FAILED,
            error_message="Adapter publish failed",
        )
        _retry_or_fail_deploy(self_task, job, exc)

    base_path = f"/weights/{meta['base_path']}"
    adapter_path = f"/weights/.adapters/{deployed.model_id}"
    record_deploy_stage(job.pk, f"Adapter published — serving on shared base {base_model_id}…")

    try:
        api_server_cls = modal.Cls.from_name(_MODAL_APP_NAME, _API_SERVER_CLS, environment_name=env)
        inference_url: str = api_server_cls().register_model.remote(
            model_id=deployed.model_id,
            weights_path=base_path,
            max_model_len=max_model_len,
            gpu_type=gpu_type,
            tokenizer_name=base_model_id,
        )
    except Exception as exc:
        logger.error("Registration failed for adapter %s: %s", deployed.model_id, exc)
        record_deploy_stage(job.pk, "Inference-server registration failed — will retry")
        DeployedModel.objects.filter(pk=deployed.pk).update(
            status=DeployedModel.Status.FAILED,
            error_message="Registration failed",
        )
        _retry_or_fail_deploy(self_task, job, exc)

    DeployedModel.objects.filter(pk=deployed.pk).update(
        weights_path=base_path,
        adapter_path=adapter_path,
        quantization=DeployedModel.Quantization.BF16,
        gpu_type=gpu_type,
        is_lora=True,
        lora_rank=meta.get("lora_rank") or 16,
        inference_url=inference_url,
        error_message="",
    )
    deployed.refresh_from_db()

    _run_pre_warm(
        self_task,
        deployed,
        job,
        env,
        weights_path=base_path,
        gpu_type=gpu_type,
        max_model_len=max_model_len,
        adapter=(deployed.model_id, f".adapters/{deployed.model_id}"),
        lora_rank=deployed.lora_rank,
    )


def _run_pre_warm(
    self_task,
    deployed,
    job,
    env,
    *,
    weights_path,
    gpu_type,
    max_model_len,
    adapter=None,
    lora_rank=0,
) -> None:
    import modal

    from overbae.models import DeployedModel, FinetuningJob

    DeployedModel.objects.filter(pk=deployed.pk).update(
        status=DeployedModel.Status.WARMING, status_changed_at=timezone.now()
    )
    logger.info("pre_warm starting for %s", deployed.model_id)
    record_deploy_stage(job.pk, "Booting the model to verify it serves…")

    try:
        pre_warm_fn = modal.Function.from_name(_MODAL_APP_NAME, "pre_warm", environment_name=env)
        with capture_modal_logs(job.pk):
            pre_warm_fn.remote(
                model_id=deployed.model_id,
                weights_path=weights_path,
                gpu_type=gpu_type,
                max_model_len=max_model_len,
                adapter=adapter,
                lora_rank=lora_rank,
            )
    except Exception as exc:
        logger.error("pre_warm failed for %s: %s", deployed.model_id, exc)
        record_deploy_stage(job.pk, "GPU pre-warm failed — will retry")
        DeployedModel.objects.filter(pk=deployed.pk).update(
            status=DeployedModel.Status.FAILED,
            error_message="Pre-warm failed",
        )
        _retry_or_fail_deploy(self_task, job, exc)

    now = timezone.now()
    DeployedModel.objects.filter(pk=deployed.pk).update(
        status=DeployedModel.Status.READY,
        error_message="",
        deployed_at=now,
    )
    record_deploy_stage(job.pk, f"Model {deployed.model_id} deployed and ready.")
    FinetuningJob.objects.filter(pk=job.pk, status=FinetuningJob.Status.DEPLOYING).update(
        status=FinetuningJob.Status.SUCCEEDED, completed_at=now
    )
    # This flip bypasses finetuning._transition, so project the graph here too.
    try:
        job.refresh_from_db()
    except Exception:  # noqa: BLE001 — graph projection must never fail deployment
        logger.warning("context graph projection failed for ft job %s", job.pk, exc_info=True)

    # Baseten final evals wait until the deployment is chat-callable, so re-tick
    # now that it is READY. Idempotent for other providers.
    try:
        from overbae.services.finetuning_eval import tick_job_evals

        tick_job_evals(job, checkpoints=None)
    except Exception:  # noqa: BLE001 — evals must never fail deployment
        logger.warning("post-READY eval tick failed for ft job %s", job.pk, exc_info=True)

    logger.info(
        "Model %s ready after pre_warm — gpu=%s url=%s weights=%s",
        deployed.model_id,
        gpu_type,
        deployed.inference_url,
        weights_path,
    )


def base_model_slug(hf_base: str) -> str:
    """The global dedupe key for base deployments: full org+name, flattened, so two orgs sharing
    a model name cannot collide."""
    flat = hf_base.replace("/", "--").replace(".", "-").replace("_", "-").lower()
    return f"base--{flat}"[:200]


@shared_task(bind=True, max_retries=5, default_retry_delay=120)
def deploy_base_model_for_eval(self, *, job_id: str) -> None:
    """Fires at job start and does not block: training runs on Baseten in parallel. One
    DeployedModel per base (keyed on ``base_model_slug``); a READY one is reused free, and Modal
    caches the weights at ``/weights/base--…`` so even a re-deploy after FAILED skips download +
    quantize.

    Never touches FinetuningJob.status — the baseline is best-effort and the training pipeline
    must not notice failures here.
    """
    import modal

    from overbae.modal.model_registry import get_hf_base
    from overbae.models import DeployedModel, FinetuningJob
    from overbae.services.finetuning_eval import (
        baseline_needs_base_deploy,
        job_wants_evals,
        tick_job_evals,
    )

    try:
        job = FinetuningJob.objects.select_related("project", "capability").get(id=job_id)
    except FinetuningJob.DoesNotExist:
        logger.error("FinetuningJob %s not found.", job_id)
        return

    if not job_wants_evals(job):
        return
    if job.status in (FinetuningJob.Status.CANCELLED, FinetuningJob.Status.FAILED):
        return

    # The baseline is the capability's incumbent model. A frontier (OpenRouter) or an
    # already-deployed self-hosted one routes directly, so the eval can fire now.
    # Only a capability with no resolvable model falls back to deploying the base.
    if not baseline_needs_base_deploy(job):
        logger.info(
            "Job %s baseline uses the capability's incumbent model — no base deploy; launching eval.",
            job_id,
        )
        tick_job_evals(job, checkpoints=None)
        return

    hf_base = get_hf_base(job.base_model)
    model_id = base_model_slug(hf_base)
    model_cfg = get_model_config_any_backend(job.base_model) or {}
    training_ctx = int((job.hyperparameters or {}).get("context_length") or 0)
    static_max_len = (model_cfg.get("inference") or {}).get("max_model_len") or 8192
    max_model_len = training_ctx if training_ctx > 0 else static_max_len
    gpu_type, _ = select_gpu(model_cfg, max_model_len)
    env = _modal_env()

    def _launch_baseline() -> None:
        job.refresh_from_db()
        if job.status in (FinetuningJob.Status.CANCELLED, FinetuningJob.Status.FAILED):
            return
        tick_job_evals(job, checkpoints=None)

    with transaction.atomic():
        deployed, created = DeployedModel.objects.get_or_create(
            model_id=model_id,
            defaults={
                "project": job.project,
                "base_model_id": hf_base,
                "gpu_type": gpu_type,
                "max_model_len": max_model_len,
                "status": DeployedModel.Status.QUEUED,
            },
        )
        if not created:
            if deployed.status == DeployedModel.Status.READY and deployed.inference_url:
                logger.info("Base model %s already READY — launching baseline eval.", model_id)
                _launch_baseline()
                return
            if deployed.status not in (
                DeployedModel.Status.FAILED,
                DeployedModel.Status.QUEUED,
            ):
                # Another job's task is driving this deployment. If it died the
                # countdown retries take over, and if those run out tick_job_evals
                # still fires the baseline once the deployment turns READY.
                raise self.retry(
                    countdown=90,
                    exc=RuntimeError(f"base deployment {model_id} in flight"),
                )
            deployed.status = DeployedModel.Status.QUEUED
            deployed.error_message = ""
            deployed.save(update_fields=["status", "error_message"])

    record_deploy_stage(job.pk, f"Deploying base model for the baseline eval ({model_id})…")
    DeployedModel.objects.filter(pk=deployed.pk).update(
        status=DeployedModel.Status.DEPLOYING, status_changed_at=timezone.now()
    )

    try:
        register_api = modal.Cls.from_name(
            _MODAL_REGISTER_APP, "RegisterAPIServer", environment_name=env
        )()
        result = register_api.register_base.remote(base_model=hf_base, model_id=model_id)
        weights_path: str = result["weights_path"]

        api_server = modal.Cls.from_name(_MODAL_APP_NAME, _API_SERVER_CLS, environment_name=env)()
        inference_url: str = api_server.register_model.remote(
            model_id=model_id,
            weights_path=weights_path,
            max_model_len=max_model_len,
            gpu_type=gpu_type,
            tokenizer_name=hf_base,
        )
        DeployedModel.objects.filter(pk=deployed.pk).update(
            weights_path=weights_path,
            quantization=DeployedModel.Quantization.FP8,
            inference_url=inference_url,
            status=DeployedModel.Status.WARMING,
            status_changed_at=timezone.now(),
            error_message="",
        )

        record_deploy_stage(job.pk, "Pre-warming base model for the baseline eval…")
        pre_warm_fn = modal.Function.from_name(_MODAL_APP_NAME, "pre_warm", environment_name=env)
        pre_warm_fn.remote(
            model_id=model_id,
            weights_path=weights_path,
            gpu_type=gpu_type,
            max_model_len=max_model_len,
        )
    except Exception as exc:
        logger.error("Base-model deploy failed for %s: %s", model_id, exc)
        record_deploy_stage(job.pk, "Base-model deploy failed — will retry")
        DeployedModel.objects.filter(pk=deployed.pk).update(
            status=DeployedModel.Status.FAILED,
            error_message="Base deploy failed",
        )
        _retry_or_fail_deploy(self, job, exc)

    DeployedModel.objects.filter(pk=deployed.pk).update(
        status=DeployedModel.Status.READY,
        deployed_at=timezone.now(),
        error_message="",
    )
    record_deploy_stage(job.pk, f"Base model ready — running baseline eval ({model_id}).")
    logger.info("Base model %s READY — launching baseline eval for job %s", model_id, job.id)
    try:
        _launch_baseline()
    except Exception:  # noqa: BLE001 — eval launch must not re-run the deploy
        logger.exception("Baseline eval launch failed after base deploy for job %s", job.id)
