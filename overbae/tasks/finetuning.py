"""Provider-specific logic lives in :mod:`overbae.services.finetuning_runner`; the active
provider comes from the ``FINETUNING_BACKEND`` environment variable."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from itertools import chain
from typing import Any

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from modal_shared.decisions import DECISION_OBJECTIVES
from modal_shared.training_telemetry import STARTUP_LABELS
from overbae.models import FinetuningJob
from overbae.services import operational_progress, training_monitoring, training_submission
from overbae.services.compute_costs import record_training_charge
from overbae.services.finetuning_runner import get_runner
from overbae.services.training_preparation import for_job, preparation_error
from overbae.tasks.training_preparation import inspect_preparation

logger = logging.getLogger(__name__)

# DEPLOYING is projected too — it is the status that carries output_model_name.

# Fail on silence, not duration. Beat is 15s; 20 misses ≈ 5 min of dead provider.
STALL_S = 30 * 60
POLL_ERROR_LIMIT = 20


@shared_task(bind=True, max_retries=4, queue="io")
def collect_training_evidence(self, job_id):
    job = FinetuningJob.objects.filter(pk=job_id).first()
    if job is None or not job.remote_job_id:
        return
    try:
        snapshot = get_runner(job.provider, job=job).poll(job.remote_job_id)
        payload = (snapshot.raw or {}).get("monitoring")
        if not payload:
            return
        summary = training_monitoring.observe(job, payload)
        with transaction.atomic():
            current = FinetuningJob.objects.select_for_update().filter(pk=job.pk).first()
            if current is None:
                return
            FinetuningJob.objects.filter(pk=job.pk).update(
                progress={**(current.progress or {}), "monitoring": summary}
            )
        if summary["collection"]["state"] == "unavailable":
            raise RuntimeError(summary["collection"]["error"])
    except Exception as exc:
        raise self.retry(exc=exc, countdown=min(30 * 2**self.request.retries, 300)) from exc


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


def _build_training_jsonl(version) -> tuple[str, int]:
    from overbae.services.datasets import rows as row_store

    row_store.verify(version)
    return _write_rows(row_store.iter_rows(version), prefix="ft-train-")


def _resolve_train_val_paths(job, supports_validation: bool) -> tuple[str, str | None, int, dict]:
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
        training_path, num_train = _build_training_jsonl(version)
        meta["train_examples"] = num_train
        return training_path, None, num_train, meta

    if job.validation_cell_id:
        training_path, num_train = _build_training_jsonl(version)
        row_store.verify(job.validation_cell)
        validation_path, num_val = _write_rows(
            row_store.iter_rows(job.validation_cell), prefix="ft-val-"
        )
        meta.update(
            {"train_examples": num_train, "val_examples": num_val, "validation_mode": "separate"}
        )
        return training_path, validation_path, num_train, meta

    row_store.verify(version)
    train_ids, val_ids, warnings = split_datapoint_ids(
        row_store.iter_rows(version),
        job.validation_split_ratio,
        method=job.split_method,
        group_by=version.dataset.source_spec.get("split", {}).get("group_by", []),
        stratify_by=version.dataset.source_spec.get("split", {}).get("stratify_by"),
    )
    train_ids, val_ids = set(train_ids), set(val_ids)
    training_path, num_train = _write_rows(
        (row for row in row_store.iter_rows(version) if row.id in train_ids), prefix="ft-train-"
    )
    validation_path, num_val = _write_rows(
        (row for row in row_store.iter_rows(version) if row.id in val_ids), prefix="ft-val-"
    )
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
    from overbae.services.finetuning_validator import validate_decision_rows, validate_rows

    with open(jsonl_path, encoding="utf-8") as source:
        rows = (json.loads(line) for line in source if line.strip())
        first = next(rows, None)
        selected = chain([first], rows) if first is not None else iter(())
        result = (
            validate_decision_rows(selected)
            if isinstance(first, dict) and "decision" in first
            else validate_rows(list(selected))
        )
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
    try:
        record_training_charge(job)
    except Exception:  # noqa: BLE001 — billing must not change training outcome
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
    job.refresh_from_db()
    operational_progress.training(job)
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


def _progress_fingerprint(progress):
    metrics = progress.get("metrics") or {}
    diagnostics = progress.get("diagnostics") or {}
    # The committer heartbeat proves liveness, not forward progress of the training process.
    return (
        progress.get("trained_steps"),
        progress.get("latest_train_loss"),
        progress.get("latest_eval_loss"),
        len(progress.get("checkpoints") or []),
        *(len(metrics.get(key) or []) for key in ("loss", "learning_rate", "grad_norm")),
        progress.get("stage") or "",
        *(
            diagnostics.get(key)
            for key in ("attempt", "completed", "checkpoint_step", "restored_step")
        ),
    )


def _persist_snapshot_progress(job, snap, *, tick_evals: bool) -> None:
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
    monitoring = (snap.raw or {}).get("monitoring") if isinstance(snap.raw, dict) else None
    if monitoring:
        progress["monitoring"] = training_monitoring.observe(job, monitoring)
        if progress["monitoring"]["collection"]["state"] == "unavailable":
            collect_training_evidence.apply_async(kwargs={"job_id": str(job.pk)}, countdown=30)
    if snap.state in {"failed", "error", "cancelled"}:
        training_monitoring.interrupt(job, reason=snap.state)
    prev = job.progress if isinstance(job.progress, dict) else {}
    for key in (
        "judge_evals",
        "preparation",
        "submission_recoveries",
        "monitoring",
        "monitoring_manifest",
    ):
        if key in prev and key not in progress:
            progress[key] = prev[key]

    if isinstance(snap.raw, dict) and "run_id" in snap.raw:
        activity = list(prev.get("activity") or [])
        if (
            not progress.get("stage")
            and prev.get("stage")
            in {"transferring", "preparing_base_model", "starting_training_worker"}
            and snap.state not in {"succeeded", "failed", "cancelled"}
        ):
            progress["stage"] = prev["stage"]
            progress["phase"] = prev.get("phase") or progress.get("phase")
            progress["diagnostics"] = prev.get("diagnostics") or {}
        stage = progress.get("stage")
        if stage and stage != prev.get("stage"):
            messages = {
                **STARTUP_LABELS,
                "loading_model": "Loading base model onto GPU",
                "initial_validation": "Evaluating base model before training",
                "training": "Training started",
                "validation": "Validating model checkpoint",
                "checkpointing": "Saving model checkpoint",
                "verifying_checkpoint": "Verifying saved checkpoint",
            }
            message = messages.get(stage, stage.replace("_", " ").capitalize())
            started = (progress.get("diagnostics") or {}).get("stage_started_at")
            activity.append(
                {
                    "ts": int(float(started) * 1000) if started else int(time.time() * 1000),
                    "kind": "stage",
                    "message": message,
                }
            )
        progress["activity"] = activity[-50:]

    metrics = progress.get("metrics") or {}
    moved = _progress_fingerprint(progress) != _progress_fingerprint(prev) and (
        progress.get("trained_steps") is not None
        or progress.get("latest_train_loss") is not None
        or progress.get("checkpoints")
        or metrics.get("loss")
        or progress.get("stage")
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
    operational_progress.training(job)

    if tick_evals:
        try:
            from overbae.services.finetuning_eval import tick_job_evals

            tick_job_evals(job)
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


def _finalize_success(job, runner, snap, remote: str) -> dict[str, Any]:
    from overbae.models import FinetuningJob

    job.refresh_from_db(fields=["status", "started_at", "progress"])
    if job.status not in (
        FinetuningJob.Status.RUNNING,
        FinetuningJob.Status.PREPARING,
        FinetuningJob.Status.QUEUED,
    ):
        return {"status": job.status, "job_id": remote}
    epoch_losses = runner.fetch_epoch_losses(remote)
    _persist_snapshot_progress(job, snap, tick_evals=False)
    progress = job.progress
    result_blob = {
        "epoch_losses": epoch_losses,
        "model": snap.output_model_name,
        "metrics": progress.get("metrics") or {},
        "checkpoints": progress.get("checkpoints") or [],
    }
    if (job.hyperparameters or {}).get("objective") in DECISION_OBJECTIVES:
        claimed = FinetuningJob.objects.filter(
            pk=job.pk,
            status__in=(
                FinetuningJob.Status.RUNNING,
                FinetuningJob.Status.PREPARING,
                FinetuningJob.Status.QUEUED,
            ),
        ).update(
            status=FinetuningJob.Status.SUCCEEDED,
            completed_at=timezone.now(),
            model_weights_location=snap.weights_url,
            output_model_name=snap.output_model_name,
            progress=progress,
            result={
                **result_blob,
                "objective": job.hyperparameters["objective"],
                "inference_contract": "typed_probabilities",
                **(snap.raw.get("native_artifact") or {}),
                "checkpoint_selection": snap.raw.get("checkpoint_selection"),
            },
        )
        job.refresh_from_db()
        if claimed:
            operational_progress.training(job)
            _record_event(job, "status_change", "Decision checkpoint saved", {"status": job.status})
            _charge_modal_finetuning(job)
        return {"status": job.status, "job_id": remote}
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
    operational_progress.training(job)
    _record_event(
        job,
        "status_change",
        message="Fine-tuning completed — deploying model",
        data={"status": FinetuningJob.Status.DEPLOYING},
    )
    _charge_modal_finetuning(job)
    try:
        from overbae.services.finetuning_eval import tick_job_evals

        tick_job_evals(job)
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
        job = FinetuningJob.objects.select_related(
            "capability", "cell__dataset", "validation_cell__dataset"
        ).get(pk=job_id)
    except FinetuningJob.DoesNotExist:
        return {"error": f"FinetuningJob {job_id} not found"}

    if job.status in (
        FinetuningJob.Status.CANCELLED,
        FinetuningJob.Status.FAILED,
        FinetuningJob.Status.SUBMISSION_UNKNOWN,
    ):
        return {"status": job.status}

    # Pin the capability's production model at submit time, or a later prod model change
    # would retroactively skew the before/after delta.
    if not job.baseline_model and job.capability_id:
        from overbae.services.finetuning_eval import resolve_baseline_model

        incumbent = resolve_baseline_model(job)
        if incumbent:
            FinetuningJob.objects.filter(pk=job.pk).update(baseline_model=incumbent)
            job.baseline_model = incumbent

    runner = get_runner(job=job)
    backend = (
        "modal"
        if (job.requested_configuration or {}).get("runtime")
        else getattr(settings, "FINETUNING_BACKEND", "baseten")
    )
    # Baseten and Modal are self-hosted-script backends whose train.py reads an
    # optional val.jsonl; Together needs its own separate eval dataset API.
    supports_validation = backend in {"baseten", "modal"}

    # An existing remote id means this is a re-drive — skip submission, go to polling.
    existing_remote_id = _remote_id(job)

    if not existing_remote_id:
        try:
            from overbae.services.finetuning_eval import start_before_evals

            provider = {
                "together": FinetuningJob.Provider.TOGETHER_AI,
                "baseten": FinetuningJob.Provider.BASETEN,
                "modal": FinetuningJob.Provider.MODAL,
            }.get(backend, FinetuningJob.Provider.BASETEN)
            FinetuningJob.objects.filter(pk=job.pk).update(provider=provider)
            job.provider = provider
            if backend == "modal":
                newly_preparing = job.status != FinetuningJob.Status.PREPARING
                if newly_preparing:
                    _transition(
                        job,
                        FinetuningJob.Status.PREPARING,
                        message="Checking training configuration",
                    )
                if newly_preparing or not (job.progress or {}).get("preparation"):
                    training_submission.record_preparation_stage(
                        job,
                        "checking_training_configuration",
                        "Checking pinned data and model configuration",
                    )
                preparation = for_job(job)
                if preparation.state in {"failed", "incompatible"}:
                    raise ValueError(preparation_error(preparation))
                job.progress = {
                    **(job.progress or {}),
                    "preparation": {
                        "id": str(preparation.id),
                        "state": preparation.state,
                        "report": preparation.report,
                        **(preparation.report.get("progress") or {}),
                    },
                }
                FinetuningJob.objects.filter(pk=job.pk).update(progress=job.progress)
                if preparation.state != "ready":
                    if preparation.state == "queued":
                        inspect_preparation.delay(str(preparation.id))
                    result = run_finetuning.apply_async(
                        kwargs={"job_id": str(job.id)}, countdown=15
                    )
                    FinetuningJob.objects.filter(pk=job.pk).update(celery_task_id=result.id)
                    return {"status": "preparing", "job_id": str(job.id)}
                job.hyperparameters = {
                    **job.hyperparameters,
                    "context_length": preparation.config["context_length"],
                }
                FinetuningJob.objects.filter(pk=job.pk).update(hyperparameters=job.hyperparameters)
            FinetuningJob.objects.filter(pk=job.pk).update(error_message="")
            if job.status != FinetuningJob.Status.PREPARING:
                _transition(job, FinetuningJob.Status.PREPARING, message="Preparing dataset")
            training_submission.record_preparation_stage(
                job, "exporting_training_rows", "Splitting and exporting training rows"
            )

            if job.eval_dataset_id and job.cell_id:
                from overbae.services.datasets import rows as row_store

                eval_version = job.eval_cell
                if eval_version is not None:
                    overlap = row_store.contamination(job.cell, eval_version)["overlap_count"]
                    if overlap:
                        _record_event(
                            job,
                            "log",
                            message=f"Warning: {overlap} training rows overlap the pinned eval dataset. Scores are not held-out estimates.",
                            data={"overlap_count": overlap, "eval_cell": str(eval_version.id)},
                        )

            training_path, validation_path, num_examples, split_meta = _resolve_train_val_paths(
                job, supports_validation
            )
            file_count = 2 if validation_path else 1
            training_submission.record_preparation_stage(
                job,
                "validating_training_files",
                f"Exported {split_meta['train_examples']:,} training rows and {split_meta['val_examples']:,} validation rows",
                completed=0,
                total=file_count,
                unit="files",
            )
            _validate_jsonl_or_fail(training_path)
            training_submission.record_preparation_stage(
                job,
                "validating_training_files",
                "Validating training files",
                completed=1,
                total=file_count,
                unit="files",
            )
            if validation_path:
                _validate_jsonl_or_fail(validation_path)
                training_submission.record_preparation_stage(
                    job,
                    "validating_training_files",
                    "Validating training files",
                    completed=2,
                    total=2,
                    unit="files",
                )

            try:
                start_before_evals(job)
            except Exception:  # noqa: BLE001 — evaluator launch is independent of training
                logger.exception("Baseline eval launch failed for job %s", job_id)

            training_submission.record_preparation_stage(
                job, "submitting_training_job", "Training files validated; submitting job"
            )

            try:
                training_submission.claim(job)
                try:
                    result = runner.submit(
                        job=job,
                        training_file_path=training_path,
                        num_examples=num_examples,
                        validation_file_path=validation_path,
                    )
                except Exception as exc:
                    if training_submission.release_before_dispatch(job):
                        raise
                    training_submission.unknown(job, exc)
                    return {"status": "submission_unknown", "job_id": str(job.id)}
            except training_submission.SubmissionUnresolvedError:
                return {"status": "submission_unknown", "job_id": str(job.id)}
            finally:
                for p in (training_path, validation_path):
                    if p and os.path.exists(p):
                        os.unlink(p)

            training_submission.acknowledge(job, result.remote_id)
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

    runner = get_runner(job.provider, job=job)
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

    _persist_snapshot_progress(job, snap, tick_evals=True)

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
