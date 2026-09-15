"""Provider-specific logic lives in :mod:`overbae.services.finetuning_runner`; the active
provider comes from the ``FINETUNING_BACKEND`` environment variable."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from typing import Any

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)

# DEPLOYING is projected too — it is the status that carries output_model_name.

# Fail on silence, not duration. Beat is 15s; 20 misses ≈ 5 min of dead provider.
STALL_S = 30 * 60
POLL_ERROR_LIMIT = 20


def _enqueue_baseten_cost_sync(job) -> None:
    from overbae.models import FinetuningJob

    if job.provider != FinetuningJob.Provider.BASETEN or not job.remote_job_id:
        return
    try:
        from overbae.tasks.baseten_billing_sync import sync_baseten_job_cost

        sync_baseten_job_cost.delay(job_id=str(job.id))
    except Exception:  # noqa: BLE001 — cost sync must never fail the training job
        logger.warning("Failed to enqueue Baseten cost sync for job %s", job.id, exc_info=True)


def _training_line(row) -> str:
    from overbae.services.finetuning_validator import row_to_finetuning_line

    return json.dumps(row_to_finetuning_line(row))


def _write_rows(rows, *, prefix: str) -> tuple[str, int]:
    """Rows → a temp JSONL the runner uploads; the caller unlinks it."""
    written = 0
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", prefix=prefix, encoding="utf-8", delete=False
    ) as f:
        path = f.name
        for row in rows:
            f.write(_training_line(row) + "\n")
            written += 1
    return path, written


def _build_training_jsonl(version, *, exclude_trace_ids: set[str] | None = None) -> tuple[str, int]:
    """``exclude_trace_ids`` keeps rows that also appear in the job's eval dataset out of
    training."""
    from overbae.services.datasets import rows as row_store

    row_store.verify(version)
    excluded = exclude_trace_ids or set()
    rows = (
        r
        for r in row_store.iter_rows(version)
        if not (r.source_trace_id and r.source_trace_id in excluded)
    )
    return _write_rows(rows, prefix="ft-train-")


def _resolve_train_val_paths(
    job, supports_validation: bool, *, exclude_trace_ids: set[str] | None = None
) -> tuple[str, str | None, int, dict]:
    """Returns ``(training_path, validation_path | None, num_train_rows, event_metadata)``."""
    from overbae.services.datasets import rows as row_store
    from overbae.services.finetuning_split import split_datapoint_ids

    meta: dict[str, Any] = {
        "train_examples": 0,
        "val_examples": 0,
        "validation_mode": "off",
        "split_method": job.split_method,
        "split_warnings": [],
    }
    version = job.cell
    if version is None:
        raise RuntimeError("This job has no pinned version.")

    if not supports_validation or not job.validation_enabled:
        training_path, num_train = _build_training_jsonl(
            version, exclude_trace_ids=exclude_trace_ids
        )
        meta["train_examples"] = num_train
        return training_path, None, num_train, meta

    if job.validation_cell_id:
        training_path, num_train = _build_training_jsonl(
            version, exclude_trace_ids=exclude_trace_ids
        )
        row_store.verify(job.validation_cell)
        validation_path, num_val = _write_rows(
            row_store.iter_rows(job.validation_cell), prefix="ft-val-"
        )
        meta.update(
            {"train_examples": num_train, "val_examples": num_val, "validation_mode": "separate"}
        )
        return training_path, validation_path, num_train, meta

    row_store.verify(version)
    excluded = exclude_trace_ids or set()
    rows = [
        r
        for r in row_store.iter_rows(version)
        if not (r.source_trace_id and r.source_trace_id in excluded)
    ]
    train_ids, val_ids, warnings = split_datapoint_ids(
        rows, job.validation_split_ratio, method=job.split_method
    )
    by_id = {r.id: r for r in rows}
    training_path, num_train = _write_rows((by_id[i] for i in train_ids), prefix="ft-train-")
    validation_path, num_val = _write_rows((by_id[i] for i in val_ids), prefix="ft-val-")
    meta.update(
        {
            "train_examples": num_train,
            "val_examples": num_val,
            "validation_mode": "split",
            "split_warnings": warnings,
        }
    )
    return training_path, validation_path, num_train, meta


def _validate_jsonl_or_fail(jsonl_path: str) -> None:
    from overbae.services.finetuning_validator import validate_rows

    rows: list[dict] = []
    with open(jsonl_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))

    result = validate_rows(rows)
    if not result.valid:
        raise RuntimeError("; ".join(result.errors) or "Dataset failed validation.")


def _record_event(job, event_type: str, message: str = "", data: dict | None = None) -> None:
    from overbae.models import FinetuningJobEvent

    FinetuningJobEvent.objects.create(
        job=job,
        event_type=event_type,
        message=message or "",
        data=data or {},
    )


def _charge_modal_finetuning(job) -> None:
    """Bill Modal GPU wall-clock on terminal transition. Never raises."""
    from decimal import Decimal

    from overbae.models import BillingService, FinetuningJob
    from overbae.services.billing_ledger import charge_credits
    from overbae.services.finetuning_runner import ModalRunner
    from overbae.services.inference_pricing import gpu_usd_per_second

    if job.provider != FinetuningJob.Provider.MODAL:
        return
    if job.cost_synced_at is not None:
        return
    if not job.started_at or not job.completed_at:
        return
    try:
        gpu_type, gpu_count = ModalRunner()._select_training_gpu(job)
    except Exception:  # noqa: BLE001 — billing must never fail the job
        logger.exception("Modal GPU selection failed for job %s", job.pk)
        return
    rate = gpu_usd_per_second(gpu_type)
    if rate is None:
        logger.warning("unpriced Modal gpu_type=%s for job %s", gpu_type, job.pk)
        return
    seconds = max(0.0, (job.completed_at - job.started_at).total_seconds())
    cost = Decimal(str(round(seconds * gpu_count * rate, 4)))
    if cost <= 0:
        return
    job.cost_usd = cost
    job.cost_synced_at = timezone.now()
    job.save(update_fields=["cost_usd", "cost_synced_at", "updated_at"])
    if not job.triggered_by_id:
        return
    try:
        charge_credits(
            job.triggered_by,
            cost,
            BillingService.FINETUNING_JOB,
            project_id=job.project_id,
            idempotency_key=f"finetuning-job:{job.id}:{cost}",
            metadata={
                "job_id": str(job.id),
                "provider": "modal",
                "gpu_type": gpu_type,
                "gpu_count": gpu_count,
                "seconds": seconds,
                "cost_usd": str(cost),
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("Failed to charge Modal finetuning job %s", job.pk)


def _transition(job, status: str, *, message: str = "", error: str = "") -> None:
    from overbae.models import FinetuningJob
    from overbae.services.finetuning_runner import sanitize_job_error

    updates: dict[str, Any] = {"status": status}
    now = timezone.now()
    if status == FinetuningJob.Status.RUNNING and job.started_at is None:
        updates["started_at"] = now
    if status in {
        FinetuningJob.Status.SUCCEEDED,
        FinetuningJob.Status.FAILED,
        FinetuningJob.Status.CANCELLED,
    }:
        updates["completed_at"] = now
    # DEPLOYING is a transient state — completed_at is set when deployment finishes
    if error:
        updates["error_message"] = sanitize_job_error(error)

    FinetuningJob.objects.filter(pk=job.pk).update(**updates)
    for k, v in updates.items():
        setattr(job, k, v)
    _record_event(
        job,
        "status_change",
        message=message or f"→ {status}",
        data={"status": status, **({"error": error} if error else {})},
    )
    if status in {
        FinetuningJob.Status.SUCCEEDED,
        FinetuningJob.Status.FAILED,
        FinetuningJob.Status.CANCELLED,
    }:
        _charge_modal_finetuning(job)


def _remote_id(job) -> str:
    return job.remote_job_id


def _persist_snapshot_progress(
    job, snap, *, last_fingerprint: str | None, tick_evals: bool
) -> str | None:
    """Returns the progress fingerprint, unchanged when nothing moved."""
    from overbae.models import FinetuningJob
    from overbae.services.finetuning_runner import progress_from_snapshot

    job.refresh_from_db(
        fields=[
            "started_at",
            "status",
            "result",
            "progress",
            "output_model_name",
            "eval_dataset_id",
            "eval_set_id",
        ]
    )
    progress = progress_from_snapshot(snap, started_at=job.started_at)
    # Preserve judge_evals already mirrored onto progress by the eval ticker.
    prev = job.progress if isinstance(job.progress, dict) else {}
    if prev.get("judge_evals"):
        progress["judge_evals"] = prev["judge_evals"]

    metrics = progress.get("metrics") or {}
    fingerprint = (
        f"{progress.get('trained_steps')}:"
        f"{progress.get('latest_train_loss')}:"
        f"{progress.get('latest_eval_loss')}:"
        f"{len(progress.get('checkpoints') or [])}:"
        f"{len(metrics.get('loss') or [])}:"
        f"{len(metrics.get('learning_rate') or [])}:"
        f"{len(metrics.get('grad_norm') or [])}"
    )
    if last_fingerprint is None:
        prev_metrics = prev.get("metrics") or {}
        last_fingerprint = (
            f"{prev.get('trained_steps')}:"
            f"{prev.get('latest_train_loss')}:"
            f"{prev.get('latest_eval_loss')}:"
            f"{len(prev.get('checkpoints') or [])}:"
            f"{len(prev_metrics.get('loss') or [])}:"
            f"{len(prev_metrics.get('learning_rate') or [])}:"
            f"{len(prev_metrics.get('grad_norm') or [])}"
        )
    moved = fingerprint != last_fingerprint and (
        progress.get("trained_steps") is not None
        or progress.get("latest_train_loss") is not None
        or progress.get("checkpoints")
        or metrics.get("loss")
    )
    observe = dict(prev.get("observe") or {})
    now_iso = timezone.now().isoformat()
    if moved:
        observe["last_move_at"] = now_iso
    else:
        observe.setdefault("last_move_at", now_iso)
    if progress.get("trained_steps") is not None:
        observe["saw_step"] = True
    observe["poll_errors"] = 0
    progress["observe"] = observe

    FinetuningJob.objects.filter(pk=job.pk).update(progress=progress)
    job.progress = progress

    if tick_evals:
        try:
            from overbae.services.finetuning_eval import tick_job_evals

            tick_job_evals(job, checkpoints=list(snap.checkpoints or []))
            job.refresh_from_db(fields=["progress"])
            progress = job.progress if isinstance(job.progress, dict) else progress
        except Exception:  # noqa: BLE001 — never abort training on eval errors
            logger.exception("In-training eval tick failed for job %s", job.pk)

    if moved:
        _record_event(
            job,
            "progress",
            message=(
                f"step {progress.get('trained_steps')}"
                if progress.get("trained_steps") is not None
                else "progress update"
            ),
            data={
                "step": progress.get("trained_steps"),
                "train_loss": progress.get("latest_train_loss"),
                "eval_loss": progress.get("latest_eval_loss"),
                "percent": progress.get("percent"),
                "eta_seconds": progress.get("eta_seconds"),
                "n_loss_points": len(metrics.get("loss") or []),
                "n_checkpoints": len(progress.get("checkpoints") or []),
            },
        )
        return fingerprint
    return last_fingerprint


def _finalize_success(job, runner, snap, remote: str) -> dict[str, Any]:
    from overbae.models import FinetuningJob
    from overbae.services.finetuning_runner import progress_from_snapshot

    job.refresh_from_db(fields=["status", "started_at", "progress"])
    if job.status not in (
        FinetuningJob.Status.RUNNING,
        FinetuningJob.Status.PREPARING,
        FinetuningJob.Status.QUEUED,
    ):
        return {"status": job.status, "job_id": remote}
    epoch_losses = runner.fetch_epoch_losses(remote)
    progress = progress_from_snapshot(snap, started_at=job.started_at)
    prev = job.progress if isinstance(job.progress, dict) else {}
    if prev.get("judge_evals"):
        progress["judge_evals"] = prev["judge_evals"]
    result_blob = {
        "epoch_losses": epoch_losses,
        "model": snap.output_model_name,
        "metrics": progress.get("metrics") or {},
        "checkpoints": progress.get("checkpoints") or [],
    }
    claimed = FinetuningJob.objects.filter(
        pk=job.pk,
        status__in=(
            FinetuningJob.Status.RUNNING,
            FinetuningJob.Status.PREPARING,
            FinetuningJob.Status.QUEUED,
        ),
    ).update(
        status=FinetuningJob.Status.DEPLOYING,
        model_weights_location=snap.weights_url,
        output_model_name=snap.output_model_name,
        progress=progress,
        result=result_blob,
    )
    if claimed != 1:
        job.refresh_from_db(fields=["status"])
        return {"status": job.status, "job_id": remote}
    job.refresh_from_db()
    _record_event(
        job,
        "status_change",
        message="Fine-tuning completed — deploying model",
        data={"status": FinetuningJob.Status.DEPLOYING},
    )
    try:
        from overbae.services.finetuning_eval import tick_job_evals

        tick_job_evals(job, checkpoints=list(progress.get("checkpoints") or []))
    except Exception:  # noqa: BLE001
        logger.exception("Final in-training eval tick failed for job %s", job.pk)
    try:
        from overbae.tasks.model_deployment import register_finetuned_model

        register_finetuned_model.delay(job_id=str(job.id))
        logger.info("Queued inference deployment for job %s", job.id)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to queue inference deployment for job %s — continuing", job.id)
    _enqueue_baseten_cost_sync(job)
    return {"status": "deploying", "job_id": remote}


@shared_task(
    name="overbae.tasks.finetuning.run_finetuning",
    autoretry_for=(),
    max_retries=0,
)
def run_finetuning(*, job_id: str) -> dict[str, Any]:
    from django.conf import settings

    from overbae.models import FinetuningJob
    from overbae.services.finetuning_runner import get_runner

    try:
        job = FinetuningJob.objects.select_related("capability").get(pk=job_id)
    except FinetuningJob.DoesNotExist:
        return {"error": f"FinetuningJob {job_id} not found"}

    if job.status == FinetuningJob.Status.CANCELLED:
        return {"status": "cancelled"}

    # Pin the capability's production model at submit time, or a later prod model change
    # would retroactively skew the before/after delta.
    if not job.baseline_model and job.capability_id:
        incumbent = (getattr(job.capability, "model", "") or "").strip()
        if incumbent:
            FinetuningJob.objects.filter(pk=job.pk).update(baseline_model=incumbent)
            job.baseline_model = incumbent

    runner = get_runner()
    backend = getattr(settings, "FINETUNING_BACKEND", "baseten")
    # Baseten and Modal are self-hosted-script backends whose train.py reads an
    # optional val.jsonl; Together needs its own separate eval dataset API.
    supports_validation = backend in {"baseten", "modal"}

    # An existing remote id means this is a re-drive — skip submission, go to polling.
    existing_remote_id = _remote_id(job)

    if not existing_remote_id:
        try:
            FinetuningJob.objects.filter(pk=job.pk).update(error_message="")
            _transition(job, FinetuningJob.Status.PREPARING, message="Preparing dataset")

            excluded: set[str] = set()
            if job.eval_dataset_id and job.cell_id:
                from overbae.services.datasets import rows as row_store

                eval_version = job.eval_dataset.active_cell
                if eval_version is not None:
                    excluded = row_store.trace_ids(job.cell) & row_store.trace_ids(eval_version)
                if excluded:
                    _record_event(
                        job,
                        "log",
                        message=(
                            f"Excluded {len(excluded)} datapoints that overlap "
                            "with the eval dataset"
                        ),
                        data={"excluded_overlapping": len(excluded)},
                    )

            training_path, validation_path, num_examples, split_meta = _resolve_train_val_paths(
                job, supports_validation, exclude_trace_ids=excluded
            )
            _validate_jsonl_or_fail(training_path)
            if validation_path:
                _validate_jsonl_or_fail(validation_path)

            # Stamp the provider BEFORE submit: the monitor and cancel view route
            # by job.provider, so a submit-time failure must still record a target.
            provider = {
                "together": FinetuningJob.Provider.TOGETHER_AI,
                "baseten": FinetuningJob.Provider.BASETEN,
                "modal": FinetuningJob.Provider.MODAL,
            }.get(backend, FinetuningJob.Provider.BASETEN)
            FinetuningJob.objects.filter(pk=job.pk).update(provider=provider)
            job.provider = provider

            try:
                result = runner.submit(
                    job=job,
                    training_file_path=training_path,
                    num_examples=num_examples,
                    validation_file_path=validation_path,
                )
            finally:
                for p in (training_path, validation_path):
                    if p and os.path.exists(p):
                        os.unlink(p)

            FinetuningJob.objects.filter(pk=job.pk).update(remote_job_id=result.remote_id)
            job.remote_job_id = result.remote_id

            val_note = ""
            if split_meta.get("val_examples"):
                val_note = f", {split_meta['val_examples']} validation"
            _record_event(
                job,
                "log",
                message=f"Materialised {split_meta['train_examples']} training{val_note} examples",
                data=split_meta,
            )

            if result.num_examples is not None:
                _record_event(
                    job,
                    "log",
                    message=f"Uploaded {result.num_examples} training examples",
                    data={"num_examples": result.num_examples},
                )

            _transition(
                job,
                FinetuningJob.Status.RUNNING,
                message=f"Submitted (remote_id={result.remote_id})",
            )
            # A LoRA deploy merges into the bf16 base, and a cold fetch runs ~20 min
            # for a 70B (bounded by weights-Volume write throughput, not the network).
            # Start it under training instead. fetch_base_model is a global mutex, so
            # the deploy either finds it done or waits on this same call.
            # Fire-and-forget: correctness does not depend on it landing.
            try:
                import modal

                from overbae.modal.model_registry import get_hf_base
                from overbae.tasks.model_deployment import _modal_env

                prefetch_id = get_hf_base(job.base_model)
                modal.Function.from_name(
                    "overmind-register", "fetch_base_model", environment_name=_modal_env()
                ).spawn(base_model=prefetch_id)
                logger.info("Prefetching base model %s for deploy (job %s)", prefetch_id, job.id)
            except Exception:  # noqa: BLE001 — prefetch is an optimisation only
                logger.warning("Base-model prefetch spawn failed for job %s", job_id, exc_info=True)
            # Baseline half of the before/after loop. Non-blocking — the Celery task
            # Modal-deploys the base only when the baseline route needs it, and
            # otherwise just ticks the eval.
            try:
                from overbae.services.finetuning_eval import job_wants_evals

                job.refresh_from_db()
                if job_wants_evals(job):
                    from overbae.tasks.model_deployment import deploy_base_model_for_eval

                    deploy_base_model_for_eval.delay(job_id=str(job.id))
                    logger.info("Queued base-model deploy for baseline eval (job %s)", job.id)
            except Exception:  # noqa: BLE001
                logger.exception("Baseline deploy enqueue failed for job %s", job_id)
            # OpenRouter and gateway baselines launch here; a base_deploy one waits
            # for the deployment to turn READY.
            try:
                from overbae.services.finetuning_eval import tick_job_evals

                tick_job_evals(job, checkpoints=[])
            except Exception:  # noqa: BLE001
                logger.exception("Baseline eval launch failed for job %s", job_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Fine-tuning submission failed for job %s", job_id)
            _handle_failure(job, str(exc))
            return {"status": "failed", "error": str(exc)}

    # Submitted (or already had a remote id). The beat observes the FunctionCall;
    # this task must not occupy a worker for the GPU lease.
    job.refresh_from_db(fields=["status", "remote_job_id"])
    if job.status == FinetuningJob.Status.QUEUED and _remote_id(job):
        _transition(job, FinetuningJob.Status.RUNNING, message="Resuming observe")
    return {"status": "running", "job_id": _remote_id(job)}


def _cancel_remote(runner, remote: str) -> None:
    try:
        runner.cancel(remote)
    except Exception:  # noqa: BLE001
        logger.warning("Remote cancel failed for %s", remote, exc_info=True)


def _fail_and_cancel(job, runner, remote: str, error: str) -> dict[str, Any]:
    if job.retry_count + 1 > job.max_retries:
        _cancel_remote(runner, remote)
    _handle_failure(job, error)
    return {"status": "failed", "job_id": remote}


def _stalled(progress: dict) -> bool:
    from datetime import datetime

    observe = progress.get("observe") or {}
    if not observe.get("saw_step"):
        return False
    raw = observe.get("last_move_at") or ""
    try:
        moved = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return False
    if timezone.is_naive(moved):
        moved = timezone.make_aware(moved, timezone.get_current_timezone())
    return (timezone.now() - moved).total_seconds() >= STALL_S


def observe_finetuning_job(job) -> dict[str, Any]:
    """One poll of a submitted remote job. Beat driver. Never blocks."""
    from overbae.models import FinetuningJob
    from overbae.services.finetuning_runner import get_runner

    job.refresh_from_db()
    if job.status == FinetuningJob.Status.CANCELLED:
        return {"status": "cancelled"}

    remote = _remote_id(job)
    if not remote:
        return {"status": job.status}

    if job.status in (FinetuningJob.Status.PREPARING, FinetuningJob.Status.QUEUED):
        _transition(
            job,
            FinetuningJob.Status.RUNNING,
            message=f"Submitted (remote_id={remote})",
        )

    if job.status not in (
        FinetuningJob.Status.RUNNING,
        FinetuningJob.Status.PREPARING,
        FinetuningJob.Status.QUEUED,
    ):
        return {"status": job.status}

    runner = get_runner(job.provider)
    try:
        snap = runner.poll(remote)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Provider poll failed for job %s", job.id)
        _record_event(job, "error", message=f"poll failed: {exc}")
        progress = dict(job.progress or {})
        observe = dict(progress.get("observe") or {})
        errors = int(observe.get("poll_errors") or 0) + 1
        observe["poll_errors"] = errors
        progress["observe"] = observe
        FinetuningJob.objects.filter(pk=job.pk).update(progress=progress)
        job.progress = progress
        if errors >= POLL_ERROR_LIMIT:
            return _fail_and_cancel(job, runner, remote, f"Provider polling kept failing: {exc}")
        return {"status": "poll_error", "error": str(exc)}

    _persist_snapshot_progress(job, snap, last_fingerprint=None, tick_evals=True)

    if runner.is_terminal_ok(snap.state) or snap.output_model_name or snap.weights_url:
        try:
            final_snap = runner.poll(remote)
            if not (final_snap.output_model_name or final_snap.weights_url):
                final_snap = snap
        except Exception:  # noqa: BLE001
            final_snap = snap
        return _finalize_success(job, runner, final_snap, remote)

    if runner.is_terminal_fail(snap.state):
        return _fail_and_cancel(job, runner, remote, snap.error or "Fine-tuning failed")

    if runner.is_terminal_cancelled(snap.state):
        if job.status != FinetuningJob.Status.CANCELLED:
            _transition(job, FinetuningJob.Status.CANCELLED, message="Fine-tuning cancelled")
            _enqueue_baseten_cost_sync(job)
        return {"status": "cancelled", "job_id": remote}

    job.refresh_from_db(fields=["status", "progress"])
    if job.status == FinetuningJob.Status.CANCELLED:
        _cancel_remote(runner, remote)
        _enqueue_baseten_cost_sync(job)
        return {"status": "cancelled"}

    if _stalled(job.progress if isinstance(job.progress, dict) else {}):
        return _fail_and_cancel(job, runner, remote, "Fine-tuning stalled — no progress")

    return {"status": "running", "job_id": remote}


def _handle_failure(job, error: str) -> None:
    from overbae.models import FinetuningJob
    from overbae.services.finetuning_runner import sanitize_job_error

    # Keep the raw reason in logs/events for ops; persist only the scrubbed form.
    logger.error("Finetuning job %s failed: %s", job.pk, error)
    error = sanitize_job_error(error)
    if job.retry_count + 1 <= job.max_retries:
        FinetuningJob.objects.filter(pk=job.pk).update(
            retry_count=job.retry_count + 1,
            status=FinetuningJob.Status.QUEUED,
            error_message=error,
        )
        _record_event(
            job,
            "error",
            message=f"retry {job.retry_count + 1}/{job.max_retries}: {error}",
        )
        run_finetuning(job_id=str(job.id))
        return

    _transition(job, FinetuningJob.Status.FAILED, error=error)
    _enqueue_baseten_cost_sync(job)
