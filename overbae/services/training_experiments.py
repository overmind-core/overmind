import hashlib
import json
import logging
import math
from datetime import datetime
from types import SimpleNamespace

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from modal_shared.decision_checkpoint_policy import validate_policy
from modal_shared.decisions import DECISION_OBJECTIVES
from overbae.api.serializers import FinetuningJobSerializer
from overbae.core.errors import InputValidationError
from overbae.models import (
    Cell,
    Dataset,
    FinetuningJob,
    NativeEvaluationPlan,
    Project,
    TrainingExperiment,
)
from overbae.services import native_evaluation, training_release
from overbae.services.datasets import rows, use
from overbae.services.plan_limits import require_plan_quota
from overbae.services.recommendation.candidates import dataset_total_tokens
from overbae.services.training_contract import dataset_objective
from overbae.services.training_forecast import forecast
from overbae.services.training_policies import (
    RuntimeProfile,
    TrainingConstraints,
    require_authorized_forecast,
)
from overbae.services.training_record import run_record
from overbae.tasks.finetuning import run_finetuning

logger = logging.getLogger(__name__)


def candidate_payload(project, variant, *, group_id=None, index=0):
    cell = (
        Cell.objects.select_related("dataset")
        .filter(pk=variant["cell"], dataset__project=project)
        .first()
    )
    development = (
        Cell.objects.select_related("dataset")
        .filter(pk=variant.get("development_cell"), dataset__project=project)
        .first()
    )
    if cell is None or (variant.get("development_cell") and development is None):
        raise InputValidationError("Select candidate data versions in this project")
    return {
        "project": str(project.pk),
        "name": variant["name"],
        "dataset": str(cell.dataset_id),
        "cell": str(cell.pk),
        "base_model": variant["base_model"],
        "hyperparameters": variant["hyperparameters"],
        "validation_enabled": development is not None,
        "validation_dataset": str(development.dataset_id) if development else None,
        "validation_cell": str(development.pk) if development else None,
        "eval_model_before": False,
        "eval_model_after": False,
        "group_id": str(group_id) if group_id else None,
        "request_key": f"experiment:{group_id}:{index}" if group_id else None,
    }


@transaction.atomic
def create(
    project,
    *,
    user,
    name,
    purpose,
    request_key,
    variants,
    evaluation=None,
    constraints=None,
    reuse_existing_predictions=False,
):
    constraints = TrainingConstraints.model_validate(constraints or {}).model_dump(
        exclude_none=True
    )
    Project.objects.select_for_update().get(pk=project.pk)
    prior = TrainingExperiment.objects.filter(project=project, request_key=request_key).first()
    if prior:
        if (
            prior.protocol.get("constraints", {}) != constraints
            or prior.protocol.get("reuse_existing_predictions", False) != reuse_existing_predictions
            or prior.name != name
            or prior.purpose != purpose
            or prior.variants != variants
            or prior.protocol.get("source_evaluation")
            != (str(evaluation.pk) if evaluation else None)
        ):
            raise InputValidationError("This key already identifies a different experiment")
        return prior
    if not isinstance(variants, list) or not 1 <= len(variants) <= 6:
        raise InputValidationError("Save one to six explicit candidates")
    if reuse_existing_predictions and evaluation is None:
        raise InputValidationError("Select a source comparison when reusing predictions")
    if evaluation and evaluation.project_id != project.pk:
        raise InputValidationError("The evaluation protocol must belong to this project")
    if evaluation and len(evaluation.config["participants"]) + len(variants) > 8:
        raise InputValidationError("A comparison supports at most eight participants")
    fingerprints = {}
    boundaries = {}
    for index, variant in enumerate(variants):
        if set(variant) - {"name", "base_model", "cell", "development_cell", "hyperparameters"}:
            raise InputValidationError("Unknown candidate configuration")
        serializer = FinetuningJobSerializer(
            data=candidate_payload(project, variant),
            context={"request": SimpleNamespace(user=user), "verify_source_files": False},
        )
        try:
            serializer.is_valid(raise_exception=True)
        except ValidationError as exc:
            raise ValidationError({"variants": {str(index): exc.detail}}) from None
        for field in ("cell", "development_cell"):
            if not variant.get(field):
                continue
            cell = Cell.objects.select_related("dataset").get(
                pk=variant[field], dataset__project=project
            )
            use.check(cell.dataset, "train", cell=cell, verify=False)
            Dataset.objects.select_for_update().get(pk=cell.dataset_id)
            use.freeze(cell)
            fingerprints[str(cell.pk)] = cell.fingerprint
        policy = variant["hyperparameters"].get("checkpoint_policy")
        if policy:
            validate_policy(policy, has_development=bool(variant.get("development_cell")))
    protocol = {
        "constraints": constraints,
        "reuse_existing_predictions": reuse_existing_predictions,
        "source_evaluation": str(evaluation.pk) if evaluation else None,
        "evaluation": evaluation.config if evaluation else None,
        "fingerprints": fingerprints,
        "boundaries": boundaries,
        "completion_requirements": [
            "all_saved_candidates_terminal",
            "native_artifact_reload_verified",
        ]
        + (["comparison_report_verified"] if evaluation else []),
        "runtime": training_release.current(),
    }
    return TrainingExperiment.objects.create(
        project=project,
        name=name,
        purpose=purpose,
        request_key=request_key,
        variants=variants,
        protocol=protocol,
    )


def dispatch(job):
    if job.status == "queued" and not job.celery_task_id:
        result = run_finetuning.apply_async(kwargs={"job_id": str(job.pk)})
        FinetuningJob.objects.filter(pk=job.pk).update(celery_task_id=result.id)


@transaction.atomic
def launch(experiment, *, user, quote_id=None):
    experiment = (
        TrainingExperiment.objects.select_for_update()
        .select_related("project")
        .get(pk=experiment.pk)
    )
    if experiment.state in {"launched", "training", "evaluating", "completed", "failed"}:
        return experiment
    if experiment.state != "prepared":
        raise InputValidationError("Prepare the saved experiment before authorizing launch")
    if experiment.protocol.get("forecast", {}).get(
        "configuration_sha256"
    ) != configuration_identity(experiment):
        raise InputValidationError("The forecast is stale for this configuration; prepare it again")
    require_authorized_forecast(experiment.protocol, quote_id)
    for cell_id, fingerprint in experiment.protocol["fingerprints"].items():
        cell = Cell.objects.select_related("dataset").get(
            pk=cell_id, dataset__project=experiment.project
        )
        if cell.fingerprint != fingerprint:
            raise InputValidationError("A frozen candidate data version changed")
    jobs = []
    for index, variant in enumerate(experiment.variants):
        require_plan_quota(user, "training_jobs")
        serializer = FinetuningJobSerializer(
            data=candidate_payload(
                experiment.project, variant, group_id=experiment.pk, index=index
            ),
            context={
                "request": SimpleNamespace(user=user),
                "training_runtime": experiment.protocol["runtime"],
                "verify_source_files": False,
            },
        )
        serializer.is_valid(raise_exception=True)
        jobs.append(serializer.save(triggered_by=user, use_case=experiment.purpose))
    source_id = experiment.protocol["source_evaluation"]
    if source_id:
        source = NativeEvaluationPlan.objects.select_related(
            "project", "final_cell__dataset", "calibration_cell__dataset"
        ).get(pk=source_id, project=experiment.project)
        config = experiment.protocol["evaluation"]
        participants = [
            *[
                {k: v for k, v in participant.items() if k != "hf_model"}
                for participant in config["participants"]
            ],
            *[
                {"key": f"candidate-{i}", "name": job.name, "kind": "trained", "job": str(job.pk)}
                for i, job in enumerate(jobs)
            ],
        ]
        experiment.evaluation = native_evaluation.create_plan(
            experiment.project,
            name=experiment.name,
            request_key=f"experiment:{experiment.pk}",
            participants=participants,
            baseline=config["baseline"],
            final_cell=source.final_cell,
            calibration_cell=source.calibration_cell,
            inference=config["inference"],
            bootstrap_samples=config["bootstrap_samples"],
            seed=config["seed"],
            triggered_by=user,
        )
        if experiment.protocol.get("reuse_existing_predictions"):
            for participant in config["participants"]:
                native_evaluation.reuse_predictions(
                    experiment.evaluation,
                    source=source,
                    participant=participant["key"],
                    source_participant=participant["key"],
                )
    if experiment.evaluation_id:
        native_evaluation.launch(experiment.evaluation, user=user)
    experiment.protocol = {
        **experiment.protocol,
        "authorization": {
            "user": str(user.pk),
            "quote_id": quote_id,
            "authorized_at": timezone.now().isoformat(),
            "scope": "saved_candidates_and_evaluation",
        },
    }
    experiment.state = "launched"
    experiment.save()
    for job in jobs:
        transaction.on_commit(lambda job=job: dispatch(job), robust=True)
    return experiment


def configuration_identity(experiment):
    fields = {
        "variants": experiment.variants,
        **{
            key: experiment.protocol.get(key)
            for key in (
                "fingerprints",
                "runtime",
                "constraints",
                "source_evaluation",
                "evaluation",
                "reuse_existing_predictions",
            )
        },
    }
    return hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()


def create_profile(project, *, user, name, request_key, variant, max_steps, max_seconds):
    if settings.FINETUNING_BACKEND != "modal":
        raise InputValidationError("Bounded runtime profiles require the Modal training backend")
    bounds = RuntimeProfile(max_steps=max_steps, max_seconds=max_seconds).model_dump()
    hp = {**variant["hyperparameters"], "max_steps": max_steps, "runtime_profile": bounds}
    return create(
        project,
        user=user,
        name=name,
        request_key=request_key,
        purpose="Bounded runtime qualification of the pinned data and recipe; not a quality comparison",
        variants=[{**variant, "hyperparameters": hp}],
    )


@transaction.atomic
def request_preparation(experiment):
    # Training tasks import this service through serializers.
    from overbae.tasks.training_experiments import prepare_experiment

    experiment = TrainingExperiment.objects.select_for_update().get(pk=experiment.pk)
    if experiment.state in {"draft", "preparation_failed"}:
        experiment.state = "preparing"
        experiment.save()
        transaction.on_commit(lambda: prepare_experiment.delay(str(experiment.pk)), robust=True)
    return experiment


def prepare(experiment):
    with transaction.atomic():
        experiment = (
            TrainingExperiment.objects.select_for_update()
            .select_related("project")
            .get(pk=experiment.pk)
        )
        if experiment.state not in {"draft", "preparing", "preparation_failed"}:
            return experiment
        previous = experiment.protocol.get("preparation_started_at")
        if previous and (timezone.now() - datetime.fromisoformat(previous)).total_seconds() < 1260:
            return experiment
        experiment.state = "preparing"
        experiment.protocol = {
            **experiment.protocol,
            "preparation_started_at": timezone.now().isoformat(),
        }
        experiment.save()
    try:
        forecasts, boundaries = [], {}
        for variant in experiment.variants:
            cell = Cell.objects.select_related("dataset").get(
                pk=variant["cell"], dataset__project=experiment.project
            )
            cells = [cell]
            if variant.get("development_cell"):
                cells.append(
                    Cell.objects.select_related("dataset").get(
                        pk=variant["development_cell"], dataset__project=experiment.project
                    )
                )
            for pinned in cells:
                rows.verify(pinned)
                if pinned.fingerprint != experiment.protocol["fingerprints"][str(pinned.pk)]:
                    raise InputValidationError("Frozen training source changed")
                if experiment.protocol["source_evaluation"]:
                    evaluation = NativeEvaluationPlan.objects.select_related(
                        "final_cell__dataset", "calibration_cell__dataset"
                    ).get(pk=experiment.protocol["source_evaluation"], project=experiment.project)
                    boundaries[str(pinned.pk)] = {
                        role: rows.contamination(pinned, suite)
                        for role, suite in {
                            "final": evaluation.final_cell,
                            "calibration": evaluation.calibration_cell,
                        }.items()
                        if suite is not None
                    }
            hp = {**variant["hyperparameters"], "objective": dataset_objective(cell)}
            tokens = round(dataset_total_tokens(cell.stats) * (hp.get("n_epochs") or 1))
            measured = forecast(
                experiment.project_id, variant["base_model"], hp, tokens=tokens, stats=cell.stats
            )
            measured["token_basis"] = (
                "source_character_estimate; exact tokenizer preparation remains separate"
            )
            forecasts.append(measured)
        binding = {
            "variants": experiment.variants,
            "fingerprints": experiment.protocol["fingerprints"],
            "runtime": experiment.protocol["runtime"],
            "constraints": experiment.protocol.get("constraints", {}),
            "forecasts": forecasts,
        }
        quote = {
            "id": hashlib.sha256(json.dumps(binding, sort_keys=True).encode()).hexdigest(),
            "variants": forecasts,
            "configuration_sha256": configuration_identity(experiment),
            "created_at": timezone.now().isoformat(),
            "scope": "planning_estimate_not_provider_spend_cap",
        }
        protocol = {**experiment.protocol, "forecast": quote, "boundaries": boundaries}
        protocol.pop("preparation_started_at", None)
        TrainingExperiment.objects.filter(pk=experiment.pk, state="preparing").update(
            state="prepared", protocol=protocol, updated_at=timezone.now()
        )
    except Exception:
        logger.exception("Experiment preparation failed: %s", experiment.pk)
        protocol = {
            **experiment.protocol,
            "preparation_error": "Preparation failed; inspect source versions and recipe diagnostics before retrying",
        }
        protocol.pop("preparation_started_at", None)
        TrainingExperiment.objects.filter(pk=experiment.pk, state="preparing").update(
            state="preparation_failed", protocol=protocol, updated_at=timezone.now()
        )
    experiment.refresh_from_db()
    return experiment


def verified_output(job):
    if job.hyperparameters.get("objective") not in DECISION_OBJECTIVES:
        return True
    result = job.result or {}
    check = result.get("reload_verification") or {}
    error, tolerance = check.get("max_absolute_error"), check.get("tolerance")
    return bool(
        result.get("artifact_identity")
        and check.get("decisions", 0) > 0
        and type(error) in {int, float}
        and type(tolerance) in {int, float}
        and math.isfinite(error)
        and 0 <= error <= tolerance <= 1e-4
    )


def reconcile(experiment):
    if experiment.state not in {"launched", "training", "evaluating", "incomplete"}:
        return
    statuses = list(
        FinetuningJob.objects.filter(
            project=experiment.project, group_id=experiment.pk
        ).values_list("status", flat=True)
    )
    if len(statuses) != len(experiment.variants):
        state = "incomplete"
    elif any(s not in {"succeeded", "failed", "cancelled"} for s in statuses):
        state = "training"
    elif any(s != "succeeded" for s in statuses):
        state = "failed"
    elif any(
        not verified_output(job)
        for job in FinetuningJob.objects.filter(project=experiment.project, group_id=experiment.pk)
    ):
        state = "incomplete"
    elif experiment.evaluation_id:
        result = NativeEvaluationPlan.objects.get(
            pk=experiment.evaluation_id, project=experiment.project
        )
        state = (
            "completed"
            if result.state == "completed"
            else "failed"
            if result.state in {"failed", "submission_unknown"}
            else "evaluating"
        )
    else:
        state = "completed"
    TrainingExperiment.objects.filter(pk=experiment.pk, state=experiment.state).update(
        state=state, updated_at=timezone.now()
    )


def flatten(value, prefix=""):
    result = {}
    for key, item in value.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict):
            result.update(flatten(item, name))
        else:
            result[name] = item
    return result


def launch_readiness(experiment):
    readiness = {
        "allowed": False,
        "scope": "saved_training_constraints",
        "constraints": experiment.protocol.get("constraints", {}),
        "reason": "Prepare the saved experiment forecast before launch.",
    }
    if experiment.state != "prepared":
        return readiness
    try:
        require_authorized_forecast(
            experiment.protocol, (experiment.protocol.get("forecast") or {}).get("id")
        )
    except InputValidationError as exc:
        return {**readiness, "reason": str(exc.detail)}
    return {**readiness, "allowed": True, "reason": ""}


def describe(experiment):
    experiment.refresh_from_db()
    variants = [flatten(v) for v in experiment.variants]
    fields = {key for variant in variants for key in variant} - {"name"}
    varying = sorted(
        key for key in fields if any(v.get(key) != variants[0].get(key) for v in variants[1:])
    )
    jobs = FinetuningJob.objects.filter(
        project=experiment.project, group_id=experiment.pk
    ).select_related("cell__dataset", "native_evaluation")
    return {
        "id": str(experiment.pk),
        "name": experiment.name,
        "purpose": experiment.purpose,
        "next_actions": ["prepare_training_experiment"]
        if experiment.state == "draft"
        else ["launch_training_experiment"]
        if launch_readiness(experiment)["allowed"]
        else ["get_job"],
        "launch_readiness": launch_readiness(experiment),
        "state": experiment.state,
        "variants": experiment.variants,
        "protocol": experiment.protocol,
        "varying_fields": varying,
        "seed_variation": "separate_from_evaluation_group_uncertainty",
        "jobs": [run_record(job) for job in jobs],
        "evaluation": str(experiment.evaluation_id) if experiment.evaluation_id else None,
    }
