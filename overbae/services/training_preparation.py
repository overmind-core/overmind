from __future__ import annotations

import gzip
import hashlib
import io
import json
import math
import tempfile
from datetime import timedelta
from pathlib import Path

import modal
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from modal.exception import ConnectionError as ModalConnectionError
from modal.exception import InternalError, NotFoundError, ServiceError

from modal_shared.decisions import DECISION_OBJECTIVE, TEXT_OBJECTIVE
from modal_shared.preparation import processor_fingerprint as asset_fingerprint
from modal_shared.preparation import validate_preparation_report
from modal_shared.stacks import train_function_name
from modal_shared.training_data import file_digest
from modal_shared.training_release import data_format_identity
from overbae.core.errors import InputValidationError
from overbae.modal.model_registry import (
    get_hf_base,
    get_model_config_any_backend,
    get_unsloth_image,
)
from overbae.modal.training_type import training_context_length, training_enabled
from overbae.models import TrainingPreparation
from overbae.services import training_release
from overbae.services.datasets import rows as row_store
from overbae.services.datasets import use as dataset_use
from overbae.services.finetuning_policy import (
    baseten_context_length,
    estimated_training_context_length,
)
from overbae.services.finetuning_validator import row_to_finetuning_line


def processor_fingerprint():
    return asset_fingerprint(Path(__file__).parent / "sft_assets")


def data_format_fingerprint():
    return data_format_identity(Path(__file__).resolve().parents[2])


def request_preparation(
    cell,
    model: str,
    context_length: int,
    *,
    validation_cell=None,
    training_type="lora",
    runtime=None,
):
    if runtime is None and settings.FINETUNING_BACKEND != "modal":
        raise InputValidationError(
            "Exact preprocessing is available for the Modal training backend."
        )
    model_config = get_model_config_any_backend(model)
    if not model_config:
        raise InputValidationError("Choose a catalog training model.")
    if training_type not in {"lora", "full"} or not training_enabled(model_config, training_type):
        raise InputValidationError("This model does not support the selected training type.")
    maximum = training_context_length(model_config, training_type)
    if maximum is not None and context_length > maximum:
        raise InputValidationError(
            f"This training configuration supports at most {maximum} context tokens."
        )
    if not 128 <= context_length <= 2_000_000:
        raise InputValidationError("Context length must be between 128 and 2,000,000 tokens.")
    context_length = baseten_context_length(model_max=maximum, requested=context_length)
    for target in (cell, validation_cell):
        if target is not None:
            if target.dataset.project_id != cell.dataset.project_id:
                raise InputValidationError("Both datasets must belong to the same project.")
            dataset_use.check(target.dataset, "train", cell=target)
    objective = (
        DECISION_OBJECTIVE
        if (cell.intent_report.get("train") or {}).get("format") == "decision"
        else TEXT_OBJECTIVE
    )
    if objective == DECISION_OBJECTIVE and training_type != "lora":
        raise InputValidationError("Native decision training currently supports LoRA only.")
    if validation_cell is not None:
        validation_objective = (
            DECISION_OBJECTIVE
            if (validation_cell.intent_report.get("train") or {}).get("format") == "decision"
            else TEXT_OBJECTIVE
        )
        if objective != validation_objective:
            raise InputValidationError("Training and validation must use the same objective.")
    runtime = runtime or training_release.current()
    config = {
        "runtime": runtime,
        "objective": objective,
        "model": model,
        "tokenizer_model": get_hf_base(model),
        "context_length": context_length,
        "training_type": training_type,
        "cell": str(cell.id),
        "fingerprint": cell.fingerprint,
        "validation_cell": str(validation_cell.id) if validation_cell else None,
        "validation_fingerprint": validation_cell.fingerprint if validation_cell else None,
        "processor": runtime["processor"] if runtime else processor_fingerprint(),
        "data_format": runtime["data_format"],
        "stack": get_unsloth_image(model),
    }
    signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    preparation, _ = TrainingPreparation.objects.get_or_create(
        signature=signature,
        defaults={
            "cell": cell,
            "validation_cell": validation_cell,
            "config": config,
            "deadline": timezone.now() + timedelta(hours=24),
        },
    )
    return preparation


def retry_preparation(preparation):
    if preparation.state != "failed" or not preparation.report.get("retryable"):
        raise InputValidationError("This preprocessing operation cannot be retried safely.")
    if preparation.remote_id:
        modal.FunctionCall.from_id(preparation.remote_id).cancel()
    TrainingPreparation.objects.filter(
        pk=preparation.pk, state="failed", touched_at=preparation.touched_at
    ).update(
        state="queued",
        remote_id="",
        report={},
        error="",
        deadline=timezone.now() + timedelta(hours=24),
        touched_at=timezone.now(),
    )
    preparation.refresh_from_db()
    return preparation


def observe_progress(prep):
    try:
        volume = modal.Volume.from_name(
            "overmind-sft", environment_name=prep.config["runtime"]["environment"]
        )
        content = bytearray()
        for chunk in volume.read_file(f"/preparations/{prep.id}/progress.json"):
            content.extend(chunk)
            if len(content) > 65536:
                return
        raw = json.loads(content)
        progress = {
            key: raw[key]
            for key in ("completed_rows", "reused_rows", "committed_shards", "updated_at")
            if isinstance(raw.get(key), (int, float)) and math.isfinite(raw[key]) and raw[key] >= 0
        }
        progress["stage"] = str(raw.get("stage", "tokenizing"))[:80]
        progress["total_rows"] = prep.cell.rows + (
            prep.validation_cell.rows if prep.validation_cell_id else 0
        )
        TrainingPreparation.objects.filter(
            pk=prep.pk, state="running", remote_id=prep.remote_id
        ).update(report={"progress": progress}, touched_at=timezone.now())
    except (
        OSError,
        ValueError,
        TypeError,
        ModalConnectionError,
        InternalError,
        ServiceError,
        NotFoundError,
    ):
        return


def advance(preparation_id):
    now = timezone.now()
    with transaction.atomic():
        prep = (
            TrainingPreparation.objects.select_for_update(of=("self",))
            .select_related("cell", "validation_cell")
            .get(pk=preparation_id)
        )
        if prep.state in {"ready", "incompatible", "failed"}:
            return
        if prep.deadline <= now:
            prep.report = {"retryable": prep.state == "queued" or bool(prep.remote_id)}
            prep.state, prep.error = "failed", "Preprocessing exceeded its 24-hour deadline."
            prep.save(update_fields=["state", "error", "report", "touched_at"])
            return
        if prep.state == "starting":
            if prep.touched_at < now - timedelta(minutes=30):
                prep.state, prep.error = (
                    "failed",
                    "Preprocessing submission was interrupted before acknowledgement.",
                )
                prep.save(update_fields=["state", "error", "touched_at"])
            return
        starting = prep.state == "queued"
        if starting:
            prep.state = "starting"
            prep.save(update_fields=["state", "touched_at"])
    try:
        if starting:
            training_release.verify(prep.config["runtime"])
            for cell in (prep.cell, prep.validation_cell):
                if cell is None:
                    continue
                row_store.verify(cell)
                expected = prep.config[
                    "fingerprint" if cell.id == prep.cell_id else "validation_fingerprint"
                ]
                if cell.fingerprint != expected:
                    raise ValueError("Dataset version changed before preprocessing.")
            with tempfile.TemporaryDirectory(prefix="training-preparation-") as directory:
                source = Path(directory) / "rows.jsonl.gz"
                with (
                    source.open("wb") as raw,
                    gzip.GzipFile(
                        filename="", fileobj=raw, mode="wb", compresslevel=1, mtime=0
                    ) as compressed,
                    io.TextIOWrapper(compressed, encoding="utf-8") as stream,
                ):
                    for cell in (prep.cell, prep.validation_cell):
                        if cell is None:
                            continue
                        for row in row_store.iter_rows(cell):
                            stream.write(
                                json.dumps(
                                    {
                                        **row_to_finetuning_line(row),
                                        "source_row": row.extra.get("source_row", row.index),
                                        "cell": str(cell.id),
                                    }
                                )
                                + "\n"
                            )
                digest = file_digest(source)
                remote = f"/preparations/{prep.id}/rows.jsonl.gz"
                volume = modal.Volume.from_name(
                    "overmind-sft", environment_name=prep.config["runtime"]["environment"]
                )
                with volume.batch_upload(force=True) as batch:
                    batch.put_file(source, remote)
            function = modal.Function.from_name(
                prep.config["runtime"]["app"],
                "prepare_" + train_function_name(prep.config["stack"]),
                environment_name=prep.config["runtime"]["environment"],
            )
            call = function.spawn(
                str(prep.id), {**prep.config, "rows_path": "/data" + remote, "rows_sha256": digest}
            )
            TrainingPreparation.objects.filter(
                pk=prep.pk, state="starting", deadline=prep.deadline
            ).update(state="running", remote_id=call.object_id, touched_at=timezone.now())
        else:
            report = validate_preparation_report(
                modal.FunctionCall.from_id(prep.remote_id).get(timeout=0)
            )
            state = (
                "failed" if report.get("error") else "ready" if report["ready"] else "incompatible"
            )
            TrainingPreparation.objects.filter(
                pk=prep.pk, state="running", remote_id=prep.remote_id
            ).update(
                state=state,
                report=report,
                error=report.get("error", ""),
                touched_at=timezone.now(),
            )
    except TimeoutError:
        if not starting:
            observe_progress(prep)
        return
    except (ModalConnectionError, InternalError, ServiceError):
        # An observer failure is not a remote failure. Keep its handle until the deadline.
        return
    except Exception as exc:
        TrainingPreparation.objects.filter(
            pk=prep.pk, state__in=["starting", "running"], deadline=prep.deadline
        ).update(
            state="failed",
            error=str(exc)[:1000],
            report={"retryable": not starting or isinstance(exc, NotFoundError)},
            touched_at=timezone.now(),
        )


def ready_for_job(job, context_length):
    prep = request_preparation(
        job.cell,
        job.base_model,
        context_length,
        runtime=training_release.for_job(job),
        validation_cell=job.validation_cell if job.validation_enabled else None,
        training_type="full"
        if (job.hyperparameters or {}).get("training_type", {}).get("type") == "Full"
        else "lora",
    )
    if prep.state != "ready":
        raise ValueError(
            prep.error or "Exact preprocessing is not ready for this training configuration."
        )
    return prep


def for_job(job):
    hp = job.hyperparameters or {}
    kind = "full" if hp.get("training_type", {}).get("type") == "Full" else "lora"
    config = get_model_config_any_backend(job.base_model) or {}
    maximum = training_context_length(config, kind)
    validation = job.validation_cell if job.validation_enabled else None
    estimated_tokens = max(
        int((cell.stats or {}).get("max_token_length") or 0)
        for cell in (job.cell, validation)
        if cell is not None
    )
    explicit_context = (
        (getattr(job, "requested_configuration", {}) or {})
        .get("configuration", {})
        .get("hyperparameters", {})
        or {}
    ).get("context_length")
    context = (
        int(explicit_context)
        if explicit_context
        else estimated_training_context_length(
            estimated_tokens,
            model_max=maximum,
            requested=int(hp.get("context_length") or 0) or None,
        )
    )
    prep = request_preparation(
        job.cell,
        job.base_model,
        context,
        validation_cell=validation,
        runtime=training_release.for_job(job),
        training_type=kind,
    )
    exact_tokens = int(prep.report.get("max_tokens") or 0)
    if (
        prep.state == "incompatible"
        and not explicit_context
        and exact_tokens > context
        and (maximum is None or exact_tokens <= maximum)
    ):
        # Recheck every row at the measured size; length can coexist with other errors.
        context = baseten_context_length(model_max=maximum, requested=exact_tokens)
        prep = request_preparation(
            job.cell,
            job.base_model,
            context,
            validation_cell=validation,
            runtime=training_release.for_job(job),
            training_type=kind,
        )
    return prep


def preparation_error(prep):
    if prep.error:
        return prep.error
    longest = int(prep.report.get("max_tokens") or 0)
    context = int(prep.config["context_length"])
    if longest > context:
        return (
            f"The longest training or validation row needs {longest:,} tokens; "
            f"the selected training context is {context:,}. "
            "Choose a model with a larger training context or restructure the affected rows "
            "in the data workshop. No rows were truncated or dropped."
        )
    return (
        f"{prep.report.get('incompatible_rows', 0)} rows are incompatible with this "
        "training configuration. Inspect the preprocessing report for the affected rows."
    )


def retry_for_job(job):
    if settings.FINETUNING_BACKEND != "modal" or job.remote_job_id:
        return
    prep = for_job(job)
    if prep.state == "failed":
        retry_preparation(prep)
    elif prep.state == "incompatible":
        raise InputValidationError(
            "Training data is incompatible with this configuration. Inspect the preprocessing report."
            if prep.error
            else preparation_error(prep)
        )
