import hashlib
import json

from django.conf import settings

from overbae.modal.model_registry import get_model_config_any_backend
from overbae.services import native_evaluation, training_release
from overbae.services.compute_costs import training_cost
from overbae.services.training_contract import contract, selection_record

EVALUATION_CHOICES = (
    "eval_model_before",
    "eval_model_after",
    "eval_incumbent_before",
    "eval_incumbent_after",
)
CONFIGURATION_FIELDS = (
    "base_model",
    "baseline_model",
    "hyperparameters",
    "validation_enabled",
    "validation_split_ratio",
    "split_method",
    "eval_judge_model",
    *EVALUATION_CHOICES,
)


def requested_configuration(values, *, runtime=None):
    selected = selection_record(
        values.get("cell"),
        validation_cell=values.get("validation_cell"),
        eval_cell=values.get("eval_cell"),
        validation_enabled=values.get("validation_enabled", True),
        validation_split_ratio=values.get("validation_split_ratio", 0.2),
        split_method=values.get("split_method", "random"),
        eval_set=values.get("eval_set"),
        evaluations={key: values.get(key, False) for key in EVALUATION_CHOICES},
    )
    configuration = {key: values[key] for key in CONFIGURATION_FIELDS if key in values}
    result = {
        "selection": selected,
        "configuration": configuration,
        "contract": contract(values.get("cell"), values.get("hyperparameters")),
        "accepted_findings": values.get("accepted_findings", []),
    }
    if settings.FINETUNING_BACKEND == "modal":
        result["runtime"] = runtime or training_release.current()
    result["fingerprint"] = hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()
    return result


def run_record(job):
    progress, result = job.progress or {}, job.result or {}
    requested = job.requested_configuration or {}
    plan = getattr(job, "native_evaluation", None)
    return {
        "native_evaluation": native_evaluation.describe(plan) if plan else None,
        "job_id": str(job.id),
        "name": job.name,
        "status": job.status,
        "contract": contract(job.cell, job.hyperparameters),
        "requested": requested or None,
        "effective": job.effective_configuration or None,
        "provider_submission": job.provider_submission or None,
        "request_key": job.request_key,
        "diagnostics": progress.get("diagnostics") or {},
        "preparation": progress.get("preparation") or None,
        "artifact": {
            key: result.get(key)
            for key in ("artifact_identity", "inference_contract", "reload_verification")
        },
        "checkpoint_selection": result.get("checkpoint_selection"),
        "qualification": {
            "catalog_eligible": get_model_config_any_backend(job.base_model) is not None,
            "exact_preparation": progress.get("preparation") or None,
            "hardware_execution": {
                "observed": (progress.get("trained_steps") or 0) > 0,
                "steps": progress.get("trained_steps"),
                "conditions": job.effective_configuration or None,
            },
            "reload_verification": result.get("reload_verification"),
            "quality": "measured" if plan and plan.state == "completed" else "unmeasured",
        },
        "cost": training_cost(job),
        "timing": {
            "created_at": job.created_at.isoformat(),
            "training_started_at": job.started_at.isoformat() if job.started_at else None,
            "elapsed_seconds": progress.get("elapsed_seconds"),
            "training_remaining_range_seconds": progress.get("eta_range_seconds"),
            "estimate_basis": progress.get("eta_basis"),
            "eta_scope": "training_only",
        },
        "quality": {
            "status": "measured" if plan and plan.state == "completed" else "unmeasured",
            "training_loss_is_benchmark_quality": False,
        },
    }
