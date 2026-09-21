from __future__ import annotations

import hashlib
import json
import os
from datetime import timedelta
from pathlib import Path

import modal
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from modal.exception import ConnectionError as ModalConnectionError
from modal.exception import InternalError, NotFoundError, ServiceError

from modal_shared.stacks import train_function_name
from overbae.modal.model_registry import (
    get_hf_base,
    get_model_config_any_backend,
    get_unsloth_image,
)
from overbae.modal.training_type import training_context_length, training_enabled
from overbae.models import TrainingPreparation
from overbae.services import finetuning_tool_validation, finetuning_validator
from overbae.services.datasets import examples
from overbae.services.datasets import rows as row_store
from overbae.services.datasets import use as dataset_use
from overbae.services.finetuning_policy import baseten_context_length
from overbae.services.finetuning_validator import row_to_finetuning_line


def processor_fingerprint():
    assets = Path(__file__).parent / "sft_assets"
    digest = hashlib.sha256()
    for path in sorted(p for p in assets.rglob("*") if p.suffix in {".py", ".jinja"}):
        digest.update(str(path.relative_to(assets)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def data_format_fingerprint():
    digest = hashlib.sha256()
    for module in (examples, finetuning_validator, finetuning_tool_validation):
        digest.update(Path(module.__file__).read_bytes())
    return digest.hexdigest()


def request_preparation(
    cell, model: str, context_length: int, *, validation_cell=None, training_type="lora"
):
    if settings.FINETUNING_BACKEND != "modal":
        raise ValueError("Exact preprocessing is available for the Modal training backend.")
    model_config = get_model_config_any_backend(model)
    if not model_config:
        raise ValueError("Choose a catalog training model.")
    if training_type not in {"lora", "full"} or not training_enabled(model_config, training_type):
        raise ValueError("This model does not support the selected training type.")
    maximum = training_context_length(model_config, training_type)
    if maximum is not None and context_length > maximum:
        raise ValueError(f"This training configuration supports at most {maximum} context tokens.")
    if not 128 <= context_length <= 2_000_000:
        raise ValueError("Context length must be between 128 and 2,000,000 tokens.")
    context_length = baseten_context_length(model_max=maximum, requested=context_length)
    for target in (cell, validation_cell):
        if target is not None:
            if target.dataset.project_id != cell.dataset.project_id:
                raise ValueError("Both datasets must belong to the same project.")
            row_store.verify(target)
            dataset_use.check(target.dataset, "train", cell=target)
    config = {
        "model": model,
        "tokenizer_model": get_hf_base(model),
        "context_length": context_length,
        "training_type": training_type,
        "cell": str(cell.id),
        "fingerprint": cell.fingerprint,
        "validation_cell": str(validation_cell.id) if validation_cell else None,
        "validation_fingerprint": validation_cell.fingerprint if validation_cell else None,
        "processor": processor_fingerprint(),
        "data_format": data_format_fingerprint(),
        "stack": get_unsloth_image(model),
    }
    signature = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    preparation, _ = TrainingPreparation.objects.get_or_create(
        signature=signature,
        defaults={
            "cell": cell,
            "validation_cell": validation_cell,
            "config": config,
            "deadline": timezone.now() + timedelta(minutes=20),
        },
    )
    return preparation


def retry_preparation(preparation):
    if preparation.state != "failed" or not preparation.report.get("retryable"):
        raise ValueError("This preprocessing operation cannot be retried safely.")
    if preparation.remote_id:
        modal.FunctionCall.from_id(preparation.remote_id).cancel()
    TrainingPreparation.objects.filter(
        pk=preparation.pk, state="failed", touched_at=preparation.touched_at
    ).update(
        state="queued",
        remote_id="",
        report={},
        error="",
        deadline=timezone.now() + timedelta(minutes=20),
        touched_at=timezone.now(),
    )
    preparation.refresh_from_db()
    return preparation


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
            prep.state, prep.error = "failed", "Preprocessing exceeded its 20-minute deadline."
            prep.save(update_fields=["state", "error", "report", "touched_at"])
            return
        if prep.state == "starting":
            if prep.touched_at < now - timedelta(minutes=2):
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
            records = []
            for cell in (prep.cell, prep.validation_cell):
                if cell is None:
                    continue
                row_store.verify(cell)
                expected = prep.config[
                    "fingerprint" if cell.id == prep.cell_id else "validation_fingerprint"
                ]
                if cell.fingerprint != expected:
                    raise ValueError("Dataset version changed before preprocessing.")
                for row in row_store.iter_rows(cell):
                    records.append(
                        {
                            **row_to_finetuning_line(row),
                            "source_row": row.extra.get("source_row", row.index),
                            "cell": str(cell.id),
                        }
                    )
            function = modal.Function.from_name(
                "overmind-sft",
                "prepare_" + train_function_name(prep.config["stack"]),
                environment_name=os.environ.get("MODAL_ENVIRONMENT") or None,
            )
            call = function.spawn(str(prep.id), {**prep.config, "rows": records})
            TrainingPreparation.objects.filter(
                pk=prep.pk, state="starting", deadline=prep.deadline
            ).update(state="running", remote_id=call.object_id, touched_at=timezone.now())
        else:
            report = modal.FunctionCall.from_id(prep.remote_id).get(timeout=0)
            TrainingPreparation.objects.filter(
                pk=prep.pk, state="running", remote_id=prep.remote_id
            ).update(
                state="ready" if report.get("ready") else "incompatible",
                report=report,
                touched_at=timezone.now(),
            )
    except TimeoutError:
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
    context = baseten_context_length(
        model_max=training_context_length(config, kind),
        requested=int(hp.get("context_length") or 0) or None,
    )
    return request_preparation(
        job.cell,
        job.base_model,
        context,
        validation_cell=job.validation_cell if job.validation_enabled else None,
        training_type=kind,
    )


def retry_for_job(job):
    if settings.FINETUNING_BACKEND != "modal" or job.remote_job_id:
        return
    prep = for_job(job)
    if prep.state == "failed":
        retry_preparation(prep)
    elif prep.state == "incompatible":
        raise ValueError(
            "The selected data is incompatible with this training configuration. "
            "Repair the affected rows or change the model settings before retrying."
        )
