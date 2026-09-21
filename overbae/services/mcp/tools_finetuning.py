"""Project-scoped fine-tuning, deployment, and inference tools."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from asgiref.sync import sync_to_async
from django.db.models import Q
from rest_framework.exceptions import ValidationError as DRFValidationError

from overbae.api.serializers import CapabilitySerializer
from overbae.core.errors import InputValidationError
from overbae.models import Capability, Dataset, DeployedModel, EvalSet, EvalSetMember, FinetuningJob
from overbae.services.capabilities import identity
from overbae.services.datasets import review
from overbae.services.datasets import use as dataset_use
from overbae.services.datasets.contract import public_intent
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.rows import RowStoreError
from overbae.services.deployed_chat import chat_with_deployed_model
from overbae.services.deployment import retry_deployment
from overbae.services.eval.eval_set import active_members
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
    PrepareTrainingInput,
    PrepareTrainingOutput,
    RetryDeploymentInput,
    RetryDeploymentOutput,
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
    InferenceUsage,
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
        ready=bool(evaluators),
        eval_set=eval_set_data,
        evaluators=evaluators,
    )


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
    capability = _resolve_capability(context, payload.capability) if payload.capability else None
    try:
        report = finetune_prerequisite_report(context.project, dataset, capability=capability)
    except RowStoreError:
        report = {
            "missing": [],
            "catalog": {},
            "n_candidates": 0,
            "recommendations": [],
            "has_tool_calling": False,
            "excluded": [],
        }
    eval_dataset = default_eval_dataset(context.project, capability)
    eval_set = default_eval_set(context.project, capability)
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
        validation = validate_dataset(str(dataset.id), cell_id=str(cell.id))
        if not validation.valid:
            missing.insert(
                0,
                "training dataset — fix the rows the validator lists, then run the notebook again",
            )
    if not evaluator_readiness.ready:
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
    eval_cell = eval_dataset.active_cell if eval_dataset is not None else None
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
        summary="Fine-tuning is ready." if ready else "Fine-tuning is not ready.",
        ready=ready,
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
    if cell is None or not cell.fits("train")[0]:
        raise MCPError(
            "finetune_not_ready",
            "Fine-tuning requires a train cell that fits the train contract.",
        )
    if find_catalog_model(payload.base_model) is None:
        raise MCPError("model_not_found", "The base model is not in the trainable model catalog.")
    try:
        estimate = estimate_for_hyperparams(
            str(dataset.id),
            base_model=payload.base_model,
            n_epochs=payload.n_epochs,
            use_lora=payload.use_lora,
            cell=cell,
        )
    except ValueError as error:
        raise MCPError(
            "finetune_invalid", "The fine-tuning estimate could not be calculated."
        ) from error
    return EstimateFinetuneOutput(
        summary="Fine-tuning estimate calculated.",
        cost_estimate=safe_json(estimate.get("cost_estimate")),
        time_estimate=FineTuneTimeEstimate.model_validate(estimate["time_estimate"]),
        trained_tokens=estimate["trained_tokens"],
        cell=mcp_cell_contract(dataset, cell, "train"),
        resource_links=[resource_link("datasets", str(dataset.id), dataset.name or "Dataset")],
    )


def _start_sync(payload: StartFinetuneInput, context: MCPContext) -> StartFinetuneOutput:
    if payload.hyperparameters is not None and _contains_sensitive_key(payload.hyperparameters):
        raise MCPError("invalid_input", "Provider credentials are not accepted in tool input.")
    dataset = _resolve_dataset(context, payload.dataset)
    cell = mcp_check(dataset, "train", _cell_ref(payload.cell, payload.version))
    capability = _resolve_capability(context, payload.capability) if payload.capability else None
    if find_catalog_model(payload.base_model) is None:
        raise MCPError("model_not_found", "The base model is not in the trainable model catalog.")

    eval_dataset = (
        _resolve_dataset(context, payload.eval_dataset)
        if payload.eval_dataset
        else default_eval_dataset(context.project, capability)
    )
    if eval_dataset is None:
        raise MCPError("finetune_not_ready", "An eval dataset is required to start fine-tuning.")
    eval_cell = mcp_check(eval_dataset, "eval", _cell_ref(payload.eval_cell, payload.eval_version))
    eval_set = (
        _resolve_eval_set(context, payload.eval_set, capability=capability)
        if payload.eval_set
        else default_eval_set(context.project, capability)
    )
    if eval_set is None:
        raise MCPError("finetune_not_ready", "An eval set is required to start fine-tuning.")

    validation_dataset = (
        _resolve_dataset(context, payload.validation_dataset)
        if payload.validation_dataset
        else None
    )
    validation_cell = None
    if validation_dataset is not None:
        validation_cell = mcp_check(
            validation_dataset,
            "train",
            _cell_ref(payload.validation_cell, payload.validation_version),
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

    catalog_entry = find_catalog_model(payload.base_model) or {}
    name = payload.name or default_finetune_name(
        display_name=str(catalog_entry.get("display") or payload.base_model),
        dataset_name=dataset.name or str(dataset.id)[:8],
        capability_name=capability.name if capability else "",
    )
    group_id = str(payload.group_id or uuid.uuid4())
    _require_credits(context)
    _require_training_quota(context)
    try:
        job = launch_finetune(
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
            eval_incumbent_before=payload.eval_incumbent_before,
            eval_incumbent_after=payload.eval_incumbent_after,
            eval_model_before=payload.eval_model_before,
            eval_model_after=payload.eval_model_after,
            baseline_model=payload.baseline_model,
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
        summary="Fine-tuning job queued.",
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
    updated = serializer.save()

    updated = Capability.objects.select_related("active_model").get(pk=updated.pk)
    capability_link = resource_link("capabilities", str(updated.id), updated.name)
    links = [capability_link]
    active_model = None
    if updated.active_model is not None:
        active_model = _deployment_reference(updated.active_model)
        links.append(active_model.resource)
    return SetActiveModelOutput(
        summary="Active model cleared." if deployment is None else "Active model updated.",
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
    if deployment.status != DeployedModel.Status.READY:
        raise MCPError(
            "deployment_not_ready",
            "Inference requires a ready deployment.",
            fields={"status": deployment.status},
        )
    _require_credits(context)
    result = chat_with_deployed_model(
        deployed=deployment,
        messages=[message.model_dump() for message in payload.messages],
        user=context.user,
        temperature=payload.temperature,
        max_tokens=payload.max_tokens,
    )
    if result.get("error"):
        raise MCPError("inference_failed", "Inference could not be completed.", retryable=True)
    usage = result.get("usage") if isinstance(result.get("usage"), dict) else None
    usage_contract = (
        InferenceUsage(
            prompt_tokens=int(usage.get("prompt_tokens") or 0),
            completion_tokens=int(usage.get("completion_tokens") or 0),
            total_tokens=(
                int(usage["total_tokens"]) if usage.get("total_tokens") is not None else None
            ),
        )
        if usage is not None
        else None
    )
    link = resource_link("deployments", str(deployment.id), deployment.model_id)
    return RunInferenceOutput(
        summary="Inference reached its output token limit."
        if result.get("truncated")
        else "Inference completed.",
        model_id=deployment.model_id,
        content=str(result.get("content") or "")[:32_000],
        usage=usage_contract,
        latency_ms=float(result.get("latency_ms") or 0),
        is_cold=bool(result.get("is_cold")),
        finish_reason=result.get("finish_reason"),
        truncated=bool(result.get("truncated")),
        content_clipped=len(str(result.get("content") or "")) > 32_000,
        resource=link,
    )


def _model_swap_prompt_sync(
    payload: GetModelSwapPromptInput, context: MCPContext
) -> GetModelSwapPromptOutput:
    from overbae.services.model_swap_prompt import model_swap_prompt_for_job

    job = _resolve_finetune(context, payload.finetune)
    result, error = model_swap_prompt_for_job(job, pin=payload.pin)
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
            validation_cell=validation.active_cell if validation else None,
            training_type=payload.training_type,
        )
        if payload.retry_failed and prep.state == "failed":
            prep = retry_preparation(prep)
    except (InputValidationError, DatasetError) as exc:
        raise MCPError("preparation_invalid", exc.detail) from exc
    if prep.state == "queued":
        inspect_preparation.delay(str(prep.id))
    return PrepareTrainingOutput(
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


def register_finetuning_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "prepare_training_data",
            "Prepare training data",
            "Run exact CPU tokenization for a model and context length. Returns a cached preparation report; call again with the same inputs to observe completion. Inspect incompatible rows and supervised content before starting training. Does not start GPU training.",
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
            "Inspect fine-tuning prerequisites, train/eval dataset shape, recommended catalog models, evaluators, and credit availability. Model ranking uses the selected capability's codebase task, or the dataset task when no capability is selected. Uncached capability classification may call an LLM.",
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
            "Estimate real-token fine-tuning cost and duration for a project dataset and catalog base model without creating a job.",
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
            "Validate and queue a fine-tuning job.",
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
            "Set or clear a capability's active model; the deployment must be ready.",
            SetActiveModelInput,
            SetActiveModelOutput,
            _set_active_sync,
            False,
            True,
            "free",
            "sync",
            {"overmind:deploy"},
        ),
        (
            "set_benchmark_model",
            "Set benchmark model",
            "Choose a ready trained deployment for future capability benchmarks; omit deployment to use the codebase incumbent. Does not change serving or existing jobs. Discover choices in the capability resource.",
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
            "Run a bounded chat completion against a ready project deployment. Returns usage, latency, finish_reason and truncated; a truncated answer is incomplete.",
            RunInferenceInput,
            RunInferenceOutput,
            _inference_sync,
            False,
            False,
            "llm",
            "sync",
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
                    "run_inference",
                },
                required_scopes=frozenset(scopes),
                cost_class=cost_class,
                async_mode=async_mode,
            ),
            _async_handler(function),
        )
