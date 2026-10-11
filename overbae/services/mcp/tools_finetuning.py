"""Project-scoped fine-tuning, deployment, and inference tools."""

from __future__ import annotations

import json
import uuid
from types import SimpleNamespace
from typing import Any

from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import Q
from rest_framework.exceptions import ValidationError as DRFValidationError

from overbae.api.serializers import CapabilitySerializer
from overbae.core.errors import InputValidationError
from overbae.models import (
    Capability,
    Cell,
    Dataset,
    DeployedModel,
    EvalSet,
    EvalSetMember,
    FinetuningJob,
)
from overbae.services import inference_requests, native_evaluation, training_monitoring
from overbae.services.capabilities import identity
from overbae.services.datasets import review
from overbae.services.datasets import use as dataset_use
from overbae.services.datasets.contract import public_intent
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.rows import RowStoreError
from overbae.services.deployment import retry_deployment
from overbae.services.eval.eval_set import active_members, snapshot_readiness
from overbae.services.finetuning_mcp import FineTuneDispatchError, launch_finetune
from overbae.services.finetuning_prereqs import (
    default_eval_dataset,
    default_eval_set,
    default_finetune_name,
    finetune_prerequisite_report,
    stamp_hyperparameters_for_model,
)
from overbae.services.finetuning_validator import ValidationResult, validate_dataset
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.common import JobReceipt
from overbae.services.mcp.contracts.finetuning import (
    CancelFinetuneInput,
    CancelFinetuneOutput,
    CheckFinetuneReadinessInput,
    CheckFinetuneReadinessOutput,
    DeploymentReference,
    EstimateFinetuneInput,
    EstimateFinetuneOutput,
    FineTuneCapabilityReadiness,
    FineTuneCreditReadiness,
    FineTuneDatasetReadiness,
    FineTuneEvalDatasetReadiness,
    FineTuneEvalSetReadiness,
    FineTuneEvaluatorReadiness,
    FineTuneJobReference,
    FineTuneTimeEstimate,
    InspectTrainingProgressInput,
    InspectTrainingProgressOutput,
    NativeEvaluationOutput,
    PrepareTrainingInput,
    PrepareTrainingOutput,
    RetryDeploymentInput,
    RetryDeploymentOutput,
    ScheduleNativeEvaluationInput,
    SetActiveModelInput,
    SetActiveModelOutput,
    SetBenchmarkModelInput,
    SetBenchmarkModelOutput,
    StartFinetuneInput,
    StartFinetuneOutput,
)
from overbae.services.mcp.contracts.inference import (
    GetModelSwapPromptInput,
    GetModelSwapPromptOutput,
    RunInferenceInput,
    RunInferenceOutput,
)
from overbae.services.mcp.errors import (
    MCPError,
    mcp_cell,
    mcp_cell_contract,
    mcp_check,
    mcp_dataset,
)
from overbae.services.mcp.resources import resource_link, safe_json
from overbae.services.recommendation import estimate_for_hyperparams, find_catalog_model
from overbae.services.training_cancellation import cancel as cancel_training
from overbae.services.training_contract import contract, selection_record
from overbae.services.training_policies import profile_options
from overbae.services.training_preparation import request_preparation, retry_preparation
from overbae.tasks.training_preparation import inspect_preparation

_SENSITIVE_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "token",
    }
)
_SENSITIVE_COMPACT_KEYS = frozenset(key.replace("_", "") for key in _SENSITIVE_KEYS)


def _uuid_ref(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError):
        return None


def _resolve_dataset(context: MCPContext, reference: str) -> Dataset:
    return mcp_dataset(context, reference)


def _resolve_capability(context: MCPContext, reference: str) -> Capability:
    query = Capability.objects.filter(
        project=context.project, status=Capability.Status.CURRENT
    ).select_related("active_model", "benchmark_model", "active_eval_set")
    normalized = _uuid_ref(reference)
    capability = query.filter(id=normalized).first() if normalized else None
    if capability is None and reference:
        capability = query.filter(
            Q(name__iexact=reference.strip()) | Q(slug__iexact=reference.strip())
        ).first()
    if capability is None:
        capability = identity.lookup(context.project.id, reference)
    if capability is None:
        raise MCPError("capability_not_found", "The capability was not found in this project.")
    return capability


def _resolve_eval_set(
    context: MCPContext, reference: str, *, capability: Capability | None
) -> EvalSet:
    query = EvalSet.objects.filter(
        Q(capability__status=Capability.Status.CURRENT) | Q(capability__isnull=True),
        project=context.project,
    ).select_related("capability")
    if capability is not None:
        query = query.filter(Q(capability=capability) | Q(capability__isnull=True))
    normalized = _uuid_ref(reference)
    eval_set = query.filter(id=normalized).first() if normalized else None
    if eval_set is None and reference:
        matches = list(query.filter(name__iexact=reference.strip()).order_by("-created_at")[:2])
        if len(matches) > 1:
            raise MCPError("eval_set_not_found", "Multiple eval sets match; use the eval set id.")
        eval_set = matches[0] if matches else None
    if eval_set is None:
        raise MCPError("eval_set_not_found", "The eval set was not found in this selection.")
    return eval_set


def _resolve_deployment(context: MCPContext, reference: str) -> DeployedModel:
    query = DeployedModel.objects.filter(project=context.project).select_related(
        "finetuning_job", "finetuning_job__capability"
    )
    normalized = _uuid_ref(reference)
    deployment = query.filter(id=normalized).first() if normalized else None
    if deployment is None:
        deployment = query.filter(model_id=reference.strip()).first()
    if deployment is None:
        raise MCPError("deployment_not_found", "The deployment was not found in this project.")
    return deployment


def _resolve_finetune(context: MCPContext, reference: str) -> FinetuningJob:
    query = FinetuningJob.objects.filter(project=context.project).select_related(
        "capability", "dataset", "deployed_model"
    )
    normalized = _uuid_ref(reference)
    job = query.filter(id=normalized).first() if normalized else None
    if job is None:
        matches = list(query.filter(name__iexact=reference.strip()).order_by("-created_at")[:2])
        if len(matches) > 1:
            raise MCPError("finetune_not_found", "Multiple fine-tuning jobs match; use the job id.")
        job = matches[0] if matches else None
    if job is None:
        raise MCPError("finetune_not_found", "The fine-tuning job was not found in this project.")
    return job


def _cell_ref(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _require_credits(context: MCPContext) -> None:
    from overbae.api.credit_gate import PaymentRequired, require_credits

    try:
        require_credits(context.user)
    except PaymentRequired as error:
        raise MCPError(
            "insufficient_credits", "This operation requires available credits."
        ) from error


def _require_training_quota(context: MCPContext) -> None:
    from overbae.services.plan_limits import PlanLimitExceeded, require_plan_quota

    try:
        require_plan_quota(context.user, "training_jobs")
    except PlanLimitExceeded as error:
        raise MCPError(
            "plan_limit_exceeded", "The training-job plan limit has been reached."
        ) from error


def _credits_available(context: MCPContext) -> bool:
    try:
        _require_credits(context)
    except MCPError:
        return False
    return True


def _serializer_error(error: DRFValidationError) -> MCPError:
    """Each field keeps the serializer's own sentence: it names the fix."""
    detail = error.detail
    fields = (
        {
            str(key): " ".join(map(str, value) if isinstance(value, list) else [str(value)])[:500]
            for key, value in detail.items()
        }
        if isinstance(detail, dict)
        else {"request": str(detail)[:500]}
    )
    wrong_intent = any("dataset; this needs" in reason for reason in fields.values())
    return MCPError(
        "dataset_intent_mismatch" if wrong_intent else "finetune_invalid",
        "The fine-tuning request was refused: " + " ".join(fields.values())[:600],
        fields=fields,
    )


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, dict):
        for key, item in value.items():
            normalized = str(key).lower().replace("-", "_")
            compact = normalized.replace("_", "")
            if (
                normalized in _SENSITIVE_KEYS
                or compact in _SENSITIVE_COMPACT_KEYS
                or any(part in normalized.split("_") for part in _SENSITIVE_KEYS)
                or any(part in compact for part in _SENSITIVE_COMPACT_KEYS)
            ):
                return True
            if _contains_sensitive_key(item):
                return True
    elif isinstance(value, list):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _deployment_reference(model: DeployedModel) -> DeploymentReference:
    link = resource_link("deployments", str(model.id), model.model_id)
    return DeploymentReference(
        id=str(model.id),
        model_id=model.model_id,
        status=model.status,
        inference_url=model.inference_url or None,
        resource=link,
    )


def _evaluator_readiness(
    capability: Capability | None, eval_set: EvalSet | None
) -> FineTuneEvaluatorReadiness:
    if eval_set is None:
        return FineTuneEvaluatorReadiness(ready=False)
    members = list(
        active_members(eval_set, EvalSetMember.Role.GENERATIVE).select_related("evaluator")
    )
    evaluators = [
        {
            "id": str(member.evaluator_id),
            "name": member.evaluator.name,
            "kind": member.evaluator.kind,
            "enabled": member.enabled,
        }
        for member in members
        if member.evaluator_id and member.evaluator is not None
    ]
    eval_set_data = FineTuneEvalSetReadiness(
        id=str(eval_set.id),
        name=eval_set.name,
        capability=eval_set.capability.slug if eval_set.capability_id else None,
        active=capability is not None and capability.active_eval_set_id == eval_set.id,
        member_count=len(evaluators),
    )
    return FineTuneEvaluatorReadiness(
        ready=snapshot_readiness(eval_set)["ready"],
        eval_set=eval_set_data,
        evaluators=evaluators,
        errors=snapshot_readiness(eval_set)["errors"],
    )


def resolve_selection(payload, context, cell, capability=None):
    native = contract(cell)["inference_contract"] == "decision"
    evaluations = {
        key: getattr(payload, key, None)
        for key in (
            "eval_model_before",
            "eval_model_after",
            "eval_incumbent_before",
            "eval_incumbent_after",
        )
    }
    for key in ("eval_model_before", "eval_model_after"):
        if evaluations[key] is None:
            evaluations[key] = not native
    evaluations = {key: bool(value) for key, value in evaluations.items()}
    evaluate = any(evaluations.values())
    evaluation = (
        _resolve_dataset(context, payload.eval_dataset)
        if payload.eval_dataset
        else (default_eval_dataset(context.project, capability) if evaluate else None)
    )
    eval_cell = (
        mcp_check(evaluation, "eval", _cell_ref(payload.eval_cell, payload.eval_version))
        if evaluation
        else None
    )
    eval_set = (
        _resolve_eval_set(context, payload.eval_set, capability=capability)
        if payload.eval_set
        else (default_eval_set(context.project, capability) if evaluate else None)
    )
    validation = (
        _resolve_dataset(context, payload.validation_dataset)
        if payload.validation_dataset
        else None
    )
    validation_cell = (
        mcp_check(
            validation, "train", _cell_ref(payload.validation_cell, payload.validation_version)
        )
        if validation
        else None
    )
    if not payload.validation_enabled and validation is not None:
        raise MCPError("invalid_input", "Enable validation to select a validation dataset.")
    return dict(
        eval_dataset=evaluation,
        eval_cell=eval_cell,
        eval_set=eval_set,
        validation_dataset=validation,
        validation_cell=validation_cell,
        evaluations=evaluations,
        evaluate=evaluate,
    )


def describe_selection(payload, cell, selected):
    return selection_record(
        cell,
        validation_cell=selected["validation_cell"],
        eval_cell=selected["eval_cell"],
        validation_enabled=payload.validation_enabled,
        validation_split_ratio=payload.validation_split_ratio,
        split_method=payload.split_method,
        eval_set=selected["eval_set"],
        evaluations=selected["evaluations"],
    )


def monitoring_policy(payload, cell=None, *, has_development=True):
    supplied = getattr(payload, "monitoring", None)
    nested = (getattr(payload, "hyperparameters", None) or {}).get("monitoring")
    value = (
        supplied.model_dump(exclude_none=True, exclude_unset=True, by_alias=True)
        if supplied
        else nested
    )
    if supplied and nested is not None and value != nested:
        raise MCPError("finetune_invalid", "Supply monitoring once, not two different policies")
    hyperparameters = getattr(payload, "hyperparameters", None) or {}
    hyperparameters = {**hyperparameters, "objective": contract(cell, hyperparameters)["objective"]}
    try:
        profile_options(hyperparameters)
        if (
            hyperparameters.get("runtime_limit_seconds") is not None
            and settings.FINETUNING_BACKEND != "modal"
        ):
            raise ValueError("runtime_limit_seconds requires Modal provider enforcement")
        return training_monitoring.resolve(
            hyperparameters,
            monitoring=value,
            has_development=has_development,
            provider=settings.FINETUNING_BACKEND,
        )
    except ValueError as exc:
        raise MCPError("finetune_invalid", str(exc)) from exc


def _readiness_sync(
    payload: CheckFinetuneReadinessInput, context: MCPContext
) -> CheckFinetuneReadinessOutput:
    dataset = _resolve_dataset(context, payload.dataset)
    if public_intent(dataset.intent) != Dataset.Intent.TRAIN:
        raise MCPError(
            "dataset_intent_mismatch",
            "Fine-tuning requires a Train dataset.",
            fields={"dataset": "Expected intent=train."},
        )
    cell = mcp_cell(dataset, _cell_ref(payload.cell, payload.version)) or dataset.active_cell
    monitoring = monitoring_policy(payload, cell, has_development=payload.validation_enabled)
    capability = _resolve_capability(context, payload.capability) if payload.capability else None
    selected = resolve_selection(payload, context, cell, capability)
    try:
        report = finetune_prerequisite_report(
            context.project,
            dataset,
            capability=capability,
            cell=cell,
            validation_cell=selected["validation_cell"],
            validation_enabled=payload.validation_enabled,
            validation_split_ratio=payload.validation_split_ratio,
            split_method=payload.split_method,
            eval_dataset=selected["eval_dataset"],
            eval_cell=selected["eval_cell"],
            eval_set=selected["eval_set"],
            evaluate=selected["evaluate"],
        )
    except RowStoreError:
        report = {
            "missing": [],
            "catalog": {},
            "n_candidates": 0,
            "recommendations": [],
            "has_tool_calling": False,
            "excluded": [],
        }
    eval_dataset, eval_set = selected["eval_dataset"], selected["eval_set"]
    evaluator_readiness = _evaluator_readiness(capability, eval_set)
    credits = FineTuneCreditReadiness(required=True, available=_credits_available(context))
    missing = [
        item
        for item in (report.get("missing") or [])
        if not str(item).startswith("training dataset")
    ]
    fits, reason = (False, "no version that ran") if cell is None else cell.fits("train")
    if cell is not None and fits:
        try:
            dataset_use.check(dataset, "train", cell=cell)
        except DatasetError as exc:
            fits, reason = False, exc.detail
    if cell is None or not fits:
        missing.insert(0, f"training dataset — {reason}")
        validation = ValidationResult(False, "unknown", 0, errors=[reason])
    else:
        validation = (
            ValidationResult(**report["validation"])
            if report.get("validation")
            else validate_dataset(
                str(dataset.id),
                cell_id=str(cell.id),
                validation_enabled=payload.validation_enabled,
                validation_split_ratio=payload.validation_split_ratio,
                split_method=payload.split_method,
                validation_dataset_id=str(selected["validation_dataset"].id)
                if selected["validation_dataset"]
                else None,
                validation_cell_id=str(selected["validation_cell"].id)
                if selected["validation_cell"]
                else None,
            )
        )
        if not validation.valid:
            missing.insert(
                0,
                "training dataset — fix the rows the validator lists, then run the notebook again",
            )
    if selected["evaluate"] and not evaluator_readiness.ready:
        missing.append("evaluator — select at least one active generative evaluator")
    if not credits.available:
        missing.append("credits — add credits before starting fine-tuning")
    ready = not missing
    dataset_data = FineTuneDatasetReadiness(
        id=str(dataset.id),
        name=dataset.name or str(dataset.id)[:8],
        intent=public_intent(dataset.intent),
        cell=mcp_cell_contract(dataset, cell, "train"),
        validation=safe_json(validation.as_dict()),
    )
    eval_cell = selected["eval_cell"]
    eval_dataset_data = (
        FineTuneEvalDatasetReadiness(
            id=str(eval_dataset.id),
            name=eval_dataset.name or str(eval_dataset.id)[:8],
            intent=public_intent(eval_dataset.intent),
            cell=mcp_cell_contract(eval_dataset, eval_cell, "eval"),
        )
        if eval_dataset is not None
        else None
    )
    links = [resource_link("datasets", str(dataset.id), dataset_data.name)]
    if capability is not None:
        links.append(resource_link("capabilities", str(capability.id), capability.name))
    if eval_dataset is not None:
        links.append(
            resource_link("datasets", str(eval_dataset.id), eval_dataset.name or "Eval dataset")
        )
    return CheckFinetuneReadinessOutput(
        monitoring=monitoring,
        selection=describe_selection(payload, cell, selected),
        training_contract=contract(cell),
        summary="Training format checks passed; task suitability is unmeasured."
        if ready
        else "Training has technical blockers.",
        ready=ready,
        assessment={
            "technical": "pass" if ready else "blocked",
            "task_suitability": "unmeasured",
            "capability_linked": dataset.capability_id == capability.pk if capability else None,
            "validation": "separate_dataset"
            if selected["validation_cell"]
            else "automatic_split"
            if payload.validation_enabled
            else "disabled",
        },
        missing=missing,
        warnings=[
            finding
            for finding in (report.get("warnings") or [])
            if not finding.startswith("training dataset:")
        ]
        + (
            [
                f"training dataset: {finding}"
                for finding in review.warnings(dataset, cell, capability=capability)
            ]
            if cell
            else []
        ),
        dataset=dataset_data,
        capability=(
            FineTuneCapabilityReadiness(
                id=str(capability.id), name=capability.name, slug=capability.slug
            )
            if capability is not None
            else None
        ),
        eval_dataset=eval_dataset_data,
        eval_set=evaluator_readiness.eval_set,
        overlap_count=report.get("overlap_count"),
        recommendations=safe_json(report.get("recommendations") or []),
        n_candidates=int(report.get("n_candidates") or 0),
        catalog=report.get("catalog") or {},
        has_tool_calling=bool(report.get("has_tool_calling")),
        task_type=report.get("task_type"),
        task_type_source=report.get("task_type_source"),
        excluded=safe_json(report.get("excluded") or []),
        recommendation_error=(
            "Model recommendations are unavailable." if report.get("recommendation_error") else None
        ),
        evaluator_readiness=evaluator_readiness,
        credits=credits,
        resource_links=links,
    )


def _estimate_sync(payload: EstimateFinetuneInput, context: MCPContext) -> EstimateFinetuneOutput:
    dataset = _resolve_dataset(context, payload.dataset)
    if public_intent(dataset.intent) != Dataset.Intent.TRAIN:
        raise MCPError(
            "dataset_intent_mismatch",
            "Fine-tuning requires a Train dataset.",
            fields={"dataset": "Expected intent=train."},
        )
    cell = mcp_cell(dataset, _cell_ref(payload.cell, payload.version)) or dataset.active_cell
    monitoring = monitoring_policy(payload, cell, has_development=payload.validation_enabled)
    if cell is None or not cell.fits("train")[0]:
        raise MCPError(
            "finetune_not_ready",
            "Fine-tuning requires a train cell that fits the train contract.",
        )
    if find_catalog_model(payload.base_model) is None:
        raise MCPError("model_not_found", "The base model is not in the trainable model catalog.")
    selected = resolve_selection(payload, context, cell)
    try:
        estimate = estimate_for_hyperparams(
            str(dataset.id),
            base_model=payload.base_model,
            n_epochs=payload.n_epochs,
            use_lora=payload.use_lora,
            cell=cell,
            validation_cell=selected["validation_cell"],
            validation_enabled=payload.validation_enabled,
            validation_split_ratio=payload.validation_split_ratio,
            split_method=payload.split_method,
            hyperparameters={**payload.hyperparameters, "monitoring": monitoring},
        )
    except InputValidationError as error:
        raise MCPError("finetune_invalid", error.detail) from error
    except ValueError as error:
        raise MCPError(
            "finetune_invalid", "The fine-tuning estimate could not be calculated."
        ) from error
    return EstimateFinetuneOutput(
        monitoring={
            "policy": monitoring,
            "cost_coverage": "Periodic validation, generation probes and checkpoint storage are not yet measured by this forecast",
            "overhead_target_is_spend_cap": False,
        },
        forecast=estimate.get("forecast"),
        selection=describe_selection(payload, cell, selected),
        training_contract=contract(cell, payload.hyperparameters),
        summary="Fine-tuning estimate calculated.",
        cost_estimate=safe_json(estimate.get("cost_estimate")),
        time_estimate=FineTuneTimeEstimate.model_validate(estimate["time_estimate"]),
        trained_tokens=estimate["trained_tokens"],
        cell=mcp_cell_contract(dataset, cell, "train"),
        resource_links=[resource_link("datasets", str(dataset.id), dataset.name or "Dataset")],
    )


def _start_sync(payload: StartFinetuneInput, context: MCPContext) -> StartFinetuneOutput:
    if payload.hyperparameters is not None and _contains_sensitive_key(
        {key: value for key, value in payload.hyperparameters.items() if key != "monitoring"}
    ):
        raise MCPError("invalid_input", "Provider credentials are not accepted in tool input.")
    dataset = _resolve_dataset(context, payload.dataset)
    cell = mcp_check(dataset, "train", _cell_ref(payload.cell, payload.version))
    capability = _resolve_capability(context, payload.capability) if payload.capability else None
    if find_catalog_model(payload.base_model) is None:
        raise MCPError("model_not_found", "The base model is not in the trainable model catalog.")

    selected = resolve_selection(payload, context, cell, capability)
    eval_dataset, eval_cell, eval_set = (
        selected["eval_dataset"],
        selected["eval_cell"],
        selected["eval_set"],
    )
    if selected["evaluate"] and (eval_dataset is None or eval_set is None):
        raise MCPError(
            "finetune_not_ready",
            "Select an evaluation dataset and set for the requested evaluations.",
        )
    validation_dataset, validation_cell = (
        selected["validation_dataset"],
        selected["validation_cell"],
    )
    validation = validate_dataset(
        str(dataset.id),
        validation_enabled=payload.validation_enabled,
        validation_split_ratio=payload.validation_split_ratio,
        validation_dataset_id=(str(validation_dataset.id) if validation_dataset else None),
        split_method=payload.split_method,
        cell_id=str(cell.id),
        validation_cell_id=str(validation_cell.id) if validation_cell else None,
    )
    if not validation.valid:
        raise MCPError(
            "finetune_not_ready",
            "The training dataset failed validation.",
            fields={"dataset": "Fix the dataset validation errors before starting."},
        )
    if payload.hyperparameters is None:
        try:
            hyperparameters = stamp_hyperparameters_for_model(
                str(dataset.id), payload.base_model, cell
            )
        except ValueError as error:
            raise MCPError(
                "finetune_invalid", "Default fine-tuning settings could not be derived."
            ) from error
    else:
        hyperparameters = payload.hyperparameters

    hyperparameters = {
        **hyperparameters,
        "monitoring": monitoring_policy(payload, cell, has_development=payload.validation_enabled),
    }

    catalog_entry = find_catalog_model(payload.base_model) or {}
    name = payload.name or default_finetune_name(
        display_name=str(catalog_entry.get("display") or payload.base_model),
        dataset_name=dataset.name or str(dataset.id)[:8],
        capability_name=capability.name if capability else "",
    )
    group_id = str(payload.group_id or uuid.uuid4())

    def admit_new_training():
        _require_credits(context)
        _require_training_quota(context)

    try:
        job = launch_finetune(
            admit_new_training=admit_new_training,
            request_key=payload.request_key,
            accepted_findings=payload.accepted_findings,
            user=context.user,
            project=context.project,
            dataset=dataset,
            capability=capability,
            eval_dataset=eval_dataset,
            eval_set=eval_set,
            base_model=payload.base_model,
            hyperparameters=hyperparameters,
            name=name,
            use_case=payload.use_case,
            validation_enabled=payload.validation_enabled,
            validation_split_ratio=payload.validation_split_ratio,
            validation_dataset=validation_dataset,
            split_method=payload.split_method,
            group_id=group_id,
            cell=cell,
            validation_cell=validation_cell,
            eval_cell=eval_cell,
            eval_incumbent_before=selected["evaluations"]["eval_incumbent_before"],
            eval_incumbent_after=selected["evaluations"]["eval_incumbent_after"],
            eval_model_before=selected["evaluations"]["eval_model_before"],
            eval_model_after=selected["evaluations"]["eval_model_after"],
            baseline_model=payload.baseline_model,
            eval_judge_model=payload.eval_judge_model,
        )
    except DRFValidationError as error:
        raise _serializer_error(error) from error
    except FineTuneDispatchError as error:
        raise MCPError(
            "finetune_dispatch_failed", "The fine-tuning job could not be queued.", retryable=True
        ) from error
    job_link = resource_link("jobs", f"finetune/{job.id}", job.name or "Fine-tuning job")
    finetune_link = resource_link("finetunes", str(job.id), job.name or "Fine-tuning job")
    job_ref = FineTuneJobReference(
        id=str(job.id), name=job.name, status=job.status, resource=finetune_link
    )
    return StartFinetuneOutput(
        summary=f"Fine-tuning job: {job.status}.",
        finetune=job_ref,
        job=FineTuneJobReference(
            id=str(job.id), name=job.name, status=job.status, resource=job_link
        ),
        cell=mcp_cell_contract(dataset, job.cell or cell, "train"),
        resource_links=[finetune_link, job_link],
    )


def _retry_deployment_sync(
    payload: RetryDeploymentInput, context: MCPContext
) -> RetryDeploymentOutput:
    deployment = _resolve_deployment(context, payload.deployment)
    job = deployment.finetuning_job
    if job is None:
        raise MCPError("deployment_invalid", "The deployment has no fine-tuning job.")
    if deployment.status not in (DeployedModel.Status.FAILED, DeployedModel.Status.DELETED):
        raise MCPError("deployment_not_ready", "Only failed or deleted deployments can be retried.")
    if job.status not in (FinetuningJob.Status.SUCCEEDED, FinetuningJob.Status.DEPLOYING):
        raise MCPError(
            "finetune_not_ready",
            "Training job has no usable checkpoint — retry the training job first.",
        )
    _require_credits(context)
    try:
        deployment = retry_deployment(deployment.pk)
    except InputValidationError as error:
        raise MCPError("deployment_not_ready", error.detail) from error
    deployment.refresh_from_db()
    link = resource_link("deployments", str(deployment.id), deployment.model_id)
    retry_link = resource_link(
        "jobs", f"deployment/{deployment.id}", f"Deployment retry: {deployment.model_id}"
    )
    return RetryDeploymentOutput(
        summary="Deployment retry queued.",
        deployment=_deployment_reference(deployment),
        retry=JobReceipt(
            kind="deployment",
            id=str(deployment.id),
            status=deployment.status,
            resource=retry_link,
        ),
        resource_links=[link, retry_link],
    )


def _set_active_sync(payload: SetActiveModelInput, context: MCPContext) -> SetActiveModelOutput:
    capability = _resolve_capability(context, payload.capability)
    deployment = _resolve_deployment(context, payload.deployment) if payload.deployment else None
    if deployment is not None and deployment.status != DeployedModel.Status.READY:
        raise MCPError("active_model_invalid", "Only a ready deployment can be made active.")
    serializer = CapabilitySerializer(
        capability,
        data={"active_model": str(deployment.id) if deployment is not None else None},
        partial=True,
        context={"request": SimpleNamespace(user=context.user)},
    )
    try:
        serializer.is_valid(raise_exception=True)
    except DRFValidationError as error:
        raise MCPError("active_model_invalid", "The active model failed validation.") from error
    try:
        updated = serializer.save()
    except DRFValidationError as error:
        raise MCPError("active_model_invalid", str(error.detail)) from error

    updated = Capability.objects.select_related("active_model", "activation").get(pk=updated.pk)
    capability_link = resource_link("capabilities", str(updated.id), updated.name)
    links = [capability_link]
    active_model = None
    if updated.active_model is not None:
        active_model = _deployment_reference(updated.active_model)
        links.append(active_model.resource)
    activation = getattr(updated, "activation", None) if deployment is not None else None
    receipt = None
    if activation is not None:
        link = resource_link("jobs", f"model_activation/{activation.pk}", "Model activation")
        links.append(link)
        receipt = JobReceipt(
            kind="model_activation", id=str(activation.pk), status=activation.stage, resource=link
        )
    return SetActiveModelOutput(
        summary="Active model cleared."
        if deployment is None
        else "Activation requested. Routing changes after verification succeeds.",
        activation=receipt,
        capability=capability_link,
        active_model=active_model,
        cleared=deployment is None,
        resource_links=links,
    )


def _set_benchmark_sync(
    payload: SetBenchmarkModelInput, context: MCPContext
) -> SetBenchmarkModelOutput:
    capability = _resolve_capability(context, payload.capability)
    deployment = _resolve_deployment(context, payload.deployment) if payload.deployment else None
    serializer = CapabilitySerializer(
        capability,
        data={"benchmark_model": str(deployment.id) if deployment else None},
        partial=True,
        context={"request": SimpleNamespace(user=context.user)},
    )
    try:
        serializer.is_valid(raise_exception=True)
    except DRFValidationError as error:
        raise MCPError(
            "benchmark_model_invalid", "Select a ready trained model in this project."
        ) from error
    serializer.save()
    link = resource_link("capabilities", str(capability.id), capability.name)
    reference = _deployment_reference(deployment) if deployment else None
    return SetBenchmarkModelOutput(
        summary="Benchmark model updated.",
        capability=link,
        benchmark_model=reference,
        model_id=deployment.model_id if deployment else capability.model,
        source="trained" if deployment else "codebase",
        resource_links=[link, reference.resource] if reference else [link],
    )


def _inference_sync(payload: RunInferenceInput, context: MCPContext) -> RunInferenceOutput:
    deployment = _resolve_deployment(context, payload.deployment)
    inputs = {
        "messages": [message.model_dump() for message in payload.messages],
        "temperature": payload.temperature,
        "max_tokens": payload.max_tokens,
    }
    try:
        request = inference_requests.recover_existing(deployment, payload.request_key, inputs)
        if request is None:
            if deployment.status != DeployedModel.Status.READY:
                raise MCPError(
                    "deployment_not_ready",
                    "Inference requires a ready deployment.",
                    fields={"status": deployment.status},
                )
            _require_credits(context)
            request = inference_requests.submit(
                deployment, context.user, payload.request_key, inputs
            )
    except InputValidationError as exc:
        message = str(exc)
        raise MCPError(
            "request_conflict" if "Request key" in message else "context_length_exceeded", message
        ) from None
    link = resource_link("jobs", f"inference_request/{request.pk}", "Inference request")
    return RunInferenceOutput(
        summary=f"Inference {request.state}. Read the returned job for its result.",
        model_id=request.payload["model_id"],
        job=JobReceipt(
            kind="inference_request", id=str(request.pk), status=request.state, resource=link
        ),
        resource=link,
    )


def _model_swap_prompt_sync(
    payload: GetModelSwapPromptInput, context: MCPContext
) -> GetModelSwapPromptOutput:
    from overbae.services.model_swap_prompt import model_swap_prompt_for_job

    job = _resolve_finetune(context, payload.finetune)
    result, error = model_swap_prompt_for_job(
        job, pin=payload.pin, base_url=context.inference_base_url
    )
    if result is None:
        raise MCPError("model_swap_prompt_not_ready", error or "The swap prompt is unavailable.")
    links = [
        resource_link("finetunes", str(job.id), job.name or "Fine-tuning job"),
        resource_link("capabilities", result["capability_id"], result["capability_name"]),
    ]
    return GetModelSwapPromptOutput(
        summary=f"Swap prompt ready: {result['old_model']} to {result['new_model']}.",
        finetune=str(job.id),
        pin=payload.pin,
        prompt=result["prompt"],
        capability_id=result["capability_id"],
        capability_name=result["capability_name"],
        old_model=result["old_model"],
        new_model=result["new_model"],
        resource_links=links,
    )


def _prepare_training_sync(payload, context):
    dataset = _resolve_dataset(context, payload.dataset)
    cell = mcp_cell(dataset, payload.cell) or dataset.active_cell
    monitoring = monitoring_policy(payload, cell)
    validation = (
        _resolve_dataset(context, payload.validation_dataset)
        if payload.validation_dataset
        else None
    )
    if cell is None or (validation is not None and validation.active_cell is None):
        raise MCPError("dataset_not_ready", "Select completed dataset versions.")
    try:
        prep = request_preparation(
            cell,
            payload.base_model,
            payload.context_length,
            validation_cell=(
                mcp_cell(validation, payload.validation_cell) or validation.active_cell
            )
            if validation
            else None,
            training_type=payload.training_type,
        )
        if payload.retry_failed and prep.state == "failed":
            prep = retry_preparation(prep)
    except (InputValidationError, DatasetError) as exc:
        raise MCPError("preparation_invalid", exc.detail) from exc
    if prep.state == "queued":
        inspect_preparation.delay(str(prep.id))
    return PrepareTrainingOutput(
        monitoring={
            "policy": monitoring,
            "sample_state": "Modal freezes the resolved training/development samples during transfer, before GPU dispatch; token preparation alone does not select the development split",
        },
        id=str(prep.id),
        state=prep.state,
        report=prep.report,
        error=prep.error,
        config=prep.config,
        job=JobReceipt(
            kind="training_preparation",
            id=str(prep.id),
            status=prep.state,
            resource=resource_link(
                "jobs", f"training_preparation/{prep.id}", "Training preparation"
            ),
        ),
    )


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def _schedule_native_evaluation_sync(payload, context):
    _require_credits(context)
    job = FinetuningJob.objects.filter(pk=payload.job, project=context.project).first()
    if job is None:
        raise MCPError("not_found", "Training job not found in this project.")
    cells = Cell.objects.select_related("dataset").filter(dataset__project=context.project)
    calibration = cells.filter(pk=payload.calibration_cell).first()
    final = cells.filter(pk=payload.final_cell).first()
    if calibration is None or final is None:
        raise MCPError("not_found", "Select calibration and final cells in this project.")
    try:
        plan = native_evaluation.schedule(job, calibration_cell=calibration, final_cell=final)
    except (ValueError, DatasetError) as exc:
        raise MCPError("invalid_input", str(exc)) from exc
    return NativeEvaluationOutput(
        summary="Native paired evaluation scheduled after verified checkpoint completion.",
        plan=native_evaluation.describe(plan),
        resource_links=[
            resource_link("jobs", f"native_evaluation/{plan.id}", "Native evaluation"),
            resource_link("finetunes", str(job.id), "Training experiment"),
        ],
    )


def inspect_training_progress_sync(payload, context):
    job = FinetuningJob.objects.filter(pk=payload.job, project=context.project).first()
    if job is None:
        raise MCPError("not_found", "Training job not found in this project")
    try:
        if sum(value is not None for value in (payload.probe, payload.check, payload.field)) > 1:
            raise ValueError("Choose a check, frozen probe or receipt field")
        detail = (
            training_monitoring.field_page(
                job, payload.field, offset=payload.offset, limit=payload.limit
            )
            if payload.field is not None
            else training_monitoring.probe_rows(
                job, payload.probe, offset=payload.offset, limit=payload.limit
            )
            if payload.probe
            else training_monitoring.examples(
                job, payload.check, offset=payload.offset, limit=payload.limit
            )
            if payload.check
            else training_monitoring.overview(job, offset=payload.offset, limit=payload.limit)
        )
    except (ValueError, training_monitoring.TrainingValidationRun.DoesNotExist) as exc:
        raise MCPError(
            "training_evidence_unavailable", "The requested training evidence is unavailable"
        ) from exc
    encoded = json.dumps(detail, cls=DjangoJSONEncoder, allow_nan=False)
    if len(encoded.encode()) > 128 * 1024:
        raise MCPError(
            "training_evidence_too_large",
            "Evidence exceeds 128 KiB; request a smaller page or a deeper receipt field. No values were clipped.",
        )
    return InspectTrainingProgressOutput(
        summary=f"Training {job.status}; recorded development evidence.",
        progress=json.loads(encoded),
        resource=resource_link("finetunes", str(job.id), "Training run"),
    )


def cancel_finetune_sync(payload, context):
    job = FinetuningJob.objects.filter(pk=payload.job, project=context.project).first()
    if job is None:
        raise MCPError("not_found", "Training job not found in this project")
    cancel_training(job)
    return CancelFinetuneOutput(
        summary=f"Training {job.status}; completed evidence retained.",
        id=job.id,
        status=job.status,
        warning=job.error_message or "",
        resource=resource_link("finetunes", str(job.id), "Training run"),
    )


def register_finetuning_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "inspect_training_progress",
            "Inspect training progress",
            "Read durable development checks, coverage, failures and verified checkpoints. Native decision checks report distribution cross entropy/Brier, applicable categorical accuracy and ordinal mean error with separate denominators. Per-question metrics and assessments have collections descriptors: pass their field JSON Pointer to page exact retained values, or append escaped keys/indices to inspect nested values. Pass check for paginated examples. Passive: never invokes a provider. Development evidence is not a final benchmark.",
            InspectTrainingProgressInput,
            InspectTrainingProgressOutput,
            inspect_training_progress_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
        (
            "cancel_finetune",
            "Cancel fine-tuning",
            "Cancel a training job and dependent evaluations, preserving completed checks and checkpoints. Remote cancellation warnings remain explicit; repeat to read the same terminal state.",
            CancelFinetuneInput,
            CancelFinetuneOutput,
            cancel_finetune_sync,
            False,
            True,
            "free",
            "sync",
            {"overmind:train"},
        ),
        (
            "schedule_native_evaluation",
            "Schedule native evaluation",
            "Schedule paid paired base/candidate GPU evaluation with frozen calibration/final suites after checkpoint verification. No serving activation.",
            ScheduleNativeEvaluationInput,
            NativeEvaluationOutput,
            _schedule_native_evaluation_sync,
            False,
            True,
            "gpu",
            "job",
            {"overmind:train"},
        ),
        (
            "prepare_training_data",
            "Prepare training data",
            "Tokenize for a model and context length. Repeat the same call to observe completion. Does not start GPU training.",
            PrepareTrainingInput,
            PrepareTrainingOutput,
            _prepare_training_sync,
            False,
            True,
            "compute",
            "job",
            {"overmind:train"},
        ),
        (
            "check_finetune_readiness",
            "Check fine-tuning readiness",
            "Inspect fine-tuning prerequisites, dataset shape, catalog models, evaluators, and credits. Ranking uses the capability task, or the dataset task when no capability is selected.",
            CheckFinetuneReadinessInput,
            CheckFinetuneReadinessOutput,
            _readiness_sync,
            True,
            True,
            "llm",
            "sync",
            {"overmind:read"},
        ),
        (
            "estimate_finetune",
            "Estimate fine-tuning",
            "Estimate the selected split without launching. Native forecasts need matching measurements; unknown costs stay unknown.",
            EstimateFinetuneInput,
            EstimateFinetuneOutput,
            _estimate_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
        (
            "start_finetune",
            "Start fine-tuning",
            "Train pinned versions. Same project request_key and recipe recover the existing job without new budget admission; changed recipes conflict. Native decisions require Modal LoRA and all chat eval flags false; preserve distributions and return typed probabilities. training_type: {type:Lora|Full}. Check readiness and cost first.",
            StartFinetuneInput,
            StartFinetuneOutput,
            _start_sync,
            False,
            False,
            "gpu",
            "job",
            {"overmind:train"},
        ),
        (
            "retry_deployment",
            "Retry deployment",
            "Retry a failed or deleted fine-tuned deployment.",
            RetryDeploymentInput,
            RetryDeploymentOutput,
            _retry_deployment_sync,
            False,
            False,
            "gpu",
            "job",
            {"overmind:deploy"},
        ),
        (
            "set_active_model",
            "Set active model",
            "Verify a ready deployment and switch the alias. Poll model_activation. Omit deployment to clear routing.",
            SetActiveModelInput,
            SetActiveModelOutput,
            _set_active_sync,
            False,
            True,
            "gpu",
            "job",
            {"overmind:deploy"},
        ),
        (
            "set_benchmark_model",
            "Set benchmark model",
            "Set the capability benchmark to a ready trained deployment, or omit it for the codebase incumbent. Does not change serving.",
            SetBenchmarkModelInput,
            SetBenchmarkModelOutput,
            _set_benchmark_sync,
            False,
            True,
            "free",
            "sync",
            {"overmind:train"},
        ),
        (
            "run_inference",
            "Run inference",
            "Submit inference with a stable request_key; identical retries reuse its durable job. Read get_job(kind=inference_request) for the result. Omitted max_tokens uses the production default.",
            RunInferenceInput,
            RunInferenceOutput,
            _inference_sync,
            False,
            True,
            "llm",
            "job",
            {"overmind:deploy"},
        ),
        (
            "get_model_swap_prompt",
            "Get model swap prompt",
            "Return a copy-paste prompt that points the capability's code at a successful "
            "fine-tune. Apply it locally; the MCP server does not edit the repository.",
            GetModelSwapPromptInput,
            GetModelSwapPromptOutput,
            _model_swap_prompt_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
    ]
    for (
        name,
        title,
        description,
        input_model,
        output_model,
        function,
        read_only,
        idempotent,
        cost_class,
        async_mode,
        scopes,
    ) in definitions:
        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=read_only,
                idempotent=idempotent,
                open_world=name
                in {
                    "start_finetune",
                    "retry_deployment",
                    "set_active_model",
                    "run_inference",
                    "cancel_finetune",
                },
                required_scopes=frozenset(scopes),
                cost_class=cost_class,
                async_mode=async_mode,
            ),
            _async_handler(function),
        )
