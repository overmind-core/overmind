"""Project-scoped MCP tools for evaluation authoring and execution."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import Q
from rest_framework.exceptions import ValidationError as DRFValidationError

from overbae.api.eval_serializers import AnnotationSerializer, EvalRunSerializer, EvalSetSerializer
from overbae.core.model_registry import judge_picker_models
from overbae.models import (
    Annotation,
    Capability,
    Dataset,
    EvalRun,
    EvalSample,
    EvalSet,
    Evaluator,
    ModelRef,
    Prompt,
)
from overbae.services.datasets.contract import public_intent
from overbae.services.eval import binding_check, comparison
from overbae.services.eval.authored import persist_specs
from overbae.services.eval.context_check import check_context
from overbae.services.eval.context_suggestions import judge_model_options
from overbae.services.eval.eval_set import active_members
from overbae.services.eval.roles import roles_for_evaluator
from overbae.services.eval.sanitation import sanitize_authored_text
from overbae.services.eval.specs import EvaluatorSpec
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.evaluations import (
    AnnotateEvaluationSampleInput,
    AnnotationOutput,
    BindingReadinessContract,
    CheckEvaluationReadinessInput,
    CheckEvaluationReadinessOutput,
    CompareEvaluationsInput,
    CompareEvaluationsOutput,
    CreateEvalSetInput,
    CreateEvalSetOutput,
    CreditReadinessContract,
    EvalSetReadinessContract,
    EvaluationDatasetContract,
    EvaluationJobContract,
    EvaluationVariantSummaryContract,
    EvaluatorReadinessContract,
    EvaluatorUpsertInput,
    EvaluatorUpsertOutput,
    RunEvaluationInput,
    RunEvaluationOutput,
)
from overbae.services.mcp.errors import (
    MCPError,
    mcp_cell,
    mcp_cell_contract,
    mcp_check,
    mcp_dataset,
)
from overbae.services.mcp.resources import resource_link, safe_json

_DEFAULT_VARIANT = {
    "label": "Captured traces",
    "mode": "existing",
    "is_baseline": True,
    "order": 0,
}


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _resolve_dataset(context: MCPContext, reference: str) -> Dataset:
    return mcp_dataset(context, reference)


def _resolve_eval_set(
    context: MCPContext, reference: str, *, capability: Capability | None = None
) -> EvalSet:
    query = EvalSet.objects.filter(project=context.project).select_related("capability")
    if capability is not None:
        query = query.filter(Q(capability=capability) | Q(capability__isnull=True))
    normalized = _uuid(reference)
    if normalized:
        eval_set = query.filter(id=normalized).first()
    else:
        matches = list(query.filter(name__iexact=reference).order_by("-created_at")[:2])
        if len(matches) > 1:
            raise MCPError("eval_set_not_found", "Multiple eval sets match; use the eval set id.")
        eval_set = matches[0] if matches else None
    if eval_set is None:
        raise MCPError("eval_set_not_found", "The eval set was not found in this project.")
    return eval_set


def _evaluator_query(context: MCPContext):
    return Evaluator.objects.filter(
        Q(project=context.project) | Q(project__isnull=True, is_managed=True)
    )


def _resolve_evaluator(context: MCPContext, reference: str) -> Evaluator:
    query = _evaluator_query(context).filter(is_archived=False)
    normalized = _uuid(reference)
    if normalized:
        evaluator = query.filter(id=normalized).first()
    else:
        project_matches = list(
            query.filter(project=context.project, name__iexact=reference).order_by("-version")[:2]
        )
        if len(project_matches) > 1:
            raise MCPError(
                "evaluator_not_found", "Multiple evaluators match; use the evaluator id."
            )
        evaluator = project_matches[0] if project_matches else None
        if evaluator is None:
            managed_matches = list(
                query.filter(project__isnull=True, name__iexact=reference).order_by("-version")[:2]
            )
            if len(managed_matches) > 1:
                raise MCPError(
                    "evaluator_not_found", "Multiple evaluators match; use the evaluator id."
                )
            evaluator = managed_matches[0] if managed_matches else None
    if evaluator is None:
        raise MCPError("evaluator_not_found", "The evaluator was not found in this project.")
    return evaluator


def _resolve_run(context: MCPContext, reference: str) -> EvalRun:
    query = EvalRun.objects.filter(project=context.project).select_related("dataset")
    normalized = _uuid(reference)
    if normalized:
        run = query.filter(id=normalized).first()
    else:
        matches = list(query.filter(name__iexact=reference).order_by("-created_at")[:2])
        if len(matches) > 1:
            raise MCPError("eval_run_not_found", "Multiple eval runs match; use the eval run id.")
        run = matches[0] if matches else None
    if run is None:
        raise MCPError("eval_run_not_found", "The eval run was not found in this project.")
    return run


def _resolve_sample(context: MCPContext, reference: str) -> EvalSample:
    normalized = _uuid(reference)
    sample = (
        EvalSample.objects.filter(id=normalized, run__project=context.project)
        .select_related("run", "variant")
        .first()
        if normalized
        else None
    )
    if sample is None:
        raise MCPError("eval_sample_not_found", "The eval sample was not found in this project.")
    return sample


def _resolve_capability(context: MCPContext, reference: str) -> Capability:
    normalized = _uuid(reference)
    query = Capability.objects.filter(project=context.project)
    capability = query.filter(id=normalized).first() if normalized else None
    if capability is None and reference:
        capability = (
            query.filter(name__iexact=reference).first()
            or query.filter(slug__iexact=reference).first()
        )
    if capability is None:
        raise MCPError("capability_not_found", "The capability was not found in this project.")
    return capability


def _cell_ref(*values: str | None) -> str | None:
    for value in values:
        if value:
            return value
    return None


def _credits_available(user) -> bool:
    from overbae.api.credit_gate import PaymentRequired, require_credits

    try:
        require_credits(user)
    except PaymentRequired:
        return False
    return True


def _binding_contract(evaluator: Evaluator, units, mode: str) -> BindingReadinessContract:
    try:
        health = binding_check.dry_run_spec(evaluator, units, mode=mode)
    except Exception:  # noqa: BLE001 — readiness must remain a safe inspection
        return BindingReadinessContract(
            mode=mode,
            status="red",
            checked_units=len(units),
            reason="The evaluator contract could not be checked.",
        )
    payload = health.to_dict()
    return BindingReadinessContract(
        mode=mode,
        status=health.status,
        checked_units=health.checked_units,
        variables=safe_json(payload.get("variables") or []),
        failing_vars=payload.get("failing_vars") or [],
        skipped_vars=payload.get("skipped_vars") or [],
        reason=str(payload.get("reason") or "")[:500],
    )


def _readiness_sync(
    payload: CheckEvaluationReadinessInput, context: MCPContext
) -> CheckEvaluationReadinessOutput:
    if payload.judge_model and payload.judge_model not in judge_picker_models():
        raise MCPError("invalid_judge_model", "Select a configured generative judge model.")
    dataset = _resolve_dataset(context, payload.dataset)
    if public_intent(dataset.intent) != Dataset.Intent.EVAL:
        if public_intent(dataset.intent) == Dataset.Intent.PENDING:
            message = (
                "Evaluation requires an eval dataset; this dataset is still pending. "
                "Set intent=eval."
            )
        else:
            message = (
                "Evaluation requires an eval dataset; "
                f"this dataset has intent={public_intent(dataset.intent)}."
            )
        raise MCPError("dataset_intent_mismatch", message)
    cell = mcp_cell(dataset, _cell_ref(payload.cell, payload.version)) or dataset.active_cell

    capability = dataset.capability
    eval_set = None
    if payload.eval_set:
        eval_set = _resolve_eval_set(context, payload.eval_set, capability=capability)
    elif capability is not None and capability.active_eval_set_id:
        eval_set = _resolve_eval_set(
            context, str(capability.active_eval_set_id), capability=capability
        )

    units = binding_check.sample_units_for_dataset(dataset, mode=payload.mode)
    readiness: list[EvaluatorReadinessContract] = []
    if eval_set is not None:
        seen: set[str] = set()
        for member in active_members(
            eval_set, "generative" if payload.mode == "generate" else "trace_scoring"
        ):
            evaluator = member.evaluator
            if evaluator is None or str(evaluator.id) in seen:
                continue
            seen.add(str(evaluator.id))
            roles = list(roles_for_evaluator(evaluator))
            applicable = ("generative" if payload.mode == "generate" else "trace_scoring") in roles
            readiness.append(
                EvaluatorReadinessContract(
                    id=str(evaluator.id),
                    name=evaluator.name,
                    kind=evaluator.kind,
                    roles=roles,
                    applicable=applicable,
                    binding=_binding_contract(evaluator, units, payload.mode),
                )
            )

    credits = CreditReadinessContract(required=True, available=_credits_available(context.user))
    binding_ready = all(
        row.applicable and row.binding.status in {"green", "amber", "skipped"} for row in readiness
    )
    ready = bool(eval_set and readiness and binding_ready and credits.available)
    dataset_data = EvaluationDatasetContract(
        id=str(dataset.id),
        name=dataset.name,
        intent=public_intent(dataset.intent),
        cell=mcp_cell_contract(dataset, cell, "eval"),
    )
    ready = ready and bool(dataset_data.cell and dataset_data.cell.fits)
    eval_set_data = (
        EvalSetReadinessContract(
            id=str(eval_set.id),
            name=eval_set.name,
            capability=eval_set.capability.slug if eval_set.capability_id else None,
            active=capability is not None and capability.active_eval_set_id == eval_set.id,
            member_count=len(readiness),
        )
        if eval_set is not None
        else None
    )
    links = [resource_link("datasets", str(dataset.id), dataset.name or "Dataset")]
    variants = [variant.model_dump() for variant in payload.variants]
    _validate_variant_refs(context, dataset, variants)
    context_checks = check_context(
        dataset=dataset,
        cell=cell,
        variants=variants,
        evaluators=[member.evaluator for member in active_members(eval_set, "generative")]
        if eval_set and payload.mode == "generate"
        else [],
        judge_model=payload.judge_model,
    )
    return CheckEvaluationReadinessOutput(
        summary="Evaluation is ready." if ready else "Evaluation is not ready.",
        ready=ready,
        context_checks=context_checks,
        judge_models=judge_model_options(context_checks),
        dataset=dataset_data,
        eval_set=eval_set_data,
        evaluators=readiness,
        credits=credits,
        resource_links=links,
    )


def _serializer_error(error: DRFValidationError, *, operation: str) -> MCPError:
    detail = error.detail
    fields: dict[str, str] = {}
    if isinstance(detail, dict):
        for key, _value in detail.items():
            fields[str(key)] = "invalid value"
        dataset_detail = str(detail.get("dataset") or "")
        if "eval dataset" in dataset_detail or "predates intents" in dataset_detail:
            return MCPError(
                "dataset_intent_mismatch", dataset_detail, fields={"dataset": "invalid"}
            )
    code = "evaluator_invalid" if operation == "upsert" else "evaluation_not_ready"
    return MCPError(code, f"The {operation} request failed validation.", fields=fields)


def _field(payload: EvaluatorUpsertInput, name: str, current: Any, default: Any = None) -> Any:
    if name in payload.model_fields_set:
        return getattr(payload, name)
    return current if current is not None else default


def _json_entries(entries: list[Any]) -> list[dict[str, Any]]:
    return [
        entry.model_dump(exclude_none=True) if hasattr(entry, "model_dump") else dict(entry)
        for entry in entries
    ]


def _choices_from_evaluator(evaluator: Evaluator | None) -> dict[str, float] | None:
    if evaluator is None:
        return None
    choices = {
        str(choice.get("label")): float(choice.get("value", 0))
        for choice in evaluator.choices or []
        if isinstance(choice, dict) and choice.get("label")
    }
    return choices or None


def _upsert_sync(payload: EvaluatorUpsertInput, context: MCPContext) -> EvaluatorUpsertOutput:
    current = _resolve_evaluator(context, payload.evaluator) if payload.evaluator else None
    if current is not None and current.project_id is None:
        raise MCPError("permission_denied", "Managed evaluators cannot be changed.")
    if current is not None and "kind" in payload.model_fields_set and payload.kind != current.kind:
        raise MCPError("evaluator_invalid", "An evaluator's kind cannot be changed.")

    capability = current.capability if current is not None else None
    if "capability" in payload.model_fields_set:
        capability = (
            _resolve_capability(context, payload.capability) if payload.capability else None
        )
    name = _field(payload, "name", current.name if current else None)
    if not name:
        raise MCPError("evaluator_invalid", "An evaluator name is required.")
    kind = _field(payload, "kind", current.kind if current else "llm_judge")
    scope = _field(payload, "scope", current.scope if current else "final_output")
    score_type = _field(payload, "score_type", current.score_type if current else "numeric")
    rubric = _field(payload, "rubric_md", current.rubric_md if current else "")
    if (
        "evaluation_prompt" in payload.model_fields_set
        and "rubric_md" not in payload.model_fields_set
    ):
        rubric = payload.evaluation_prompt
    rubric, _ = sanitize_authored_text(str(rubric or ""))
    checklist = _field(payload, "checklist", current.checklist if current else []) or []
    checklist = _json_entries(checklist)
    checklist = [
        {**item, "q": sanitize_authored_text(str(item.get("q") or ""))[0]} for item in checklist
    ]
    config = _field(payload, "config", current.config if current else {}) or {}
    provenance = (config.get("provenance") if isinstance(config, dict) else None) or {
        "source": "mcp:upsert_evaluator",
        "generator": "mcp@v1",
        "surface_area": "output_contract",
    }
    variable_mapping = (
        _field(
            payload,
            "variable_mapping",
            current.variable_mapping if current else [],
        )
        or []
    )
    variable_mapping = _json_entries(variable_mapping)
    choices = _field(payload, "choices", _choices_from_evaluator(current))
    spec_payload = {
        "name": name,
        "display_name": _field(payload, "display_name", current.display_name if current else "")
        or "",
        "description": _field(payload, "description", current.description if current else "") or "",
        "kind": kind,
        "scope": scope,
        "score_type": score_type,
        "rubric_md": rubric,
        "checklist": checklist,
        "judge_model": _field(payload, "judge_model", current.judge_model if current else "") or "",
        "score_min": _field(payload, "score_min", current.score_min if current else 0.0),
        "score_max": _field(payload, "score_max", current.score_max if current else 1.0),
        "pass_threshold": _field(
            payload, "pass_threshold", current.pass_threshold if current else None
        ),
        "requires_reference": _field(
            payload, "requires_reference", current.requires_reference if current else False
        ),
        "variable_mapping": variable_mapping,
        "config": config,
        "applicable_roles": _field(
            payload, "applicable_roles", current.applicable_roles if current else []
        )
        or [],
        "surface": _field(payload, "surface", current.surface if current else "any") or "any",
        "choices": choices,
        "provenance": provenance,
    }
    try:
        spec = EvaluatorSpec.model_validate(spec_payload)
    except Exception as error:
        raise MCPError(
            "evaluator_invalid", "The evaluator configuration is not supported."
        ) from error

    with transaction.atomic():
        if current is None:
            evaluator = persist_specs(
                [spec], project=context.project, capability=capability, created_by=context.user
            )[0]
            status = "created"
        else:
            values = spec.to_evaluator_kwargs()
            for field in (
                "name",
                "display_name",
                "description",
                "kind",
                "scope",
                "rubric_md",
                "checklist",
                "judge_model",
                "score_type",
                "score_min",
                "score_max",
                "choices",
                "pass_threshold",
                "requires_reference",
                "evidence_requirement",
                "surface",
                "applicable_roles",
                "variable_mapping",
                "config",
                "spec_data",
            ):
                if field in values:
                    setattr(current, field, values[field])
            current.capability = capability
            current.save()
            evaluator = current
            status = "updated"

    link = resource_link("project", "current", f"Evaluator {evaluator.name}")
    return EvaluatorUpsertOutput(
        summary=f"Evaluator {status}.",
        status=status,
        id=str(evaluator.id),
        name=evaluator.name,
        version=evaluator.version,
        kind=evaluator.kind,
        scope=evaluator.scope,
        score_type=evaluator.score_type,
        resource=link,
        resource_links=[link],
    )


def _validate_variant_refs(
    context: MCPContext, dataset: Dataset, variants: list[dict[str, Any]]
) -> None:
    for variant in variants:
        if variant.get("model_ref"):
            model_ref = _uuid(str(variant["model_ref"]))
            if (
                model_ref is None
                or not ModelRef.objects.filter(id=model_ref, project=context.project).exists()
            ):
                raise MCPError(
                    "evaluation_not_ready",
                    "The model reference is not available in this project.",
                )
        if variant.get("prompt"):
            prompt_id = _uuid(str(variant["prompt"]))
            prompt = (
                Prompt.objects.filter(id=prompt_id, capability__project=context.project).first()
                if prompt_id
                else None
            )
            if prompt is None or (
                dataset.capability_id and prompt.capability_id != dataset.capability_id
            ):
                raise MCPError(
                    "evaluation_not_ready", "The prompt is not available for this dataset."
                )


def _run_sync(payload: RunEvaluationInput, context: MCPContext) -> RunEvaluationOutput:
    from overbae.api.credit_gate import PaymentRequired, require_credits
    from overbae.tasks.eval import run_eval_run

    dataset = _resolve_dataset(context, payload.dataset)
    cell = mcp_check(dataset, "eval", _cell_ref(payload.cell, payload.version))

    eval_set = None
    if payload.eval_set:
        eval_set = _resolve_eval_set(context, payload.eval_set, capability=dataset.capability)
    elif (
        not payload.evaluator_ids
        and dataset.capability_id
        and dataset.capability.active_eval_set_id
    ):
        eval_set = _resolve_eval_set(
            context, str(dataset.capability.active_eval_set_id), capability=dataset.capability
        )
    elif not payload.evaluator_ids:
        raise MCPError(
            "evaluation_not_ready",
            "Provide evaluator ids or an eval set; this dataset's capability has no active eval set.",
        )

    evaluators = [_resolve_evaluator(context, ref) for ref in payload.evaluator_ids]
    variants = [variant.model_dump(exclude_none=True) for variant in payload.variants]
    if not variants:
        variants = [_DEFAULT_VARIANT.copy()]
    _validate_variant_refs(context, dataset, variants)

    try:
        require_credits(context.user)
    except PaymentRequired as error:
        raise MCPError(
            "insufficient_credits", "Evaluation runs require available credits."
        ) from error

    serializer_data: dict[str, Any] = {
        "project": str(context.project.id),
        "name": payload.name,
        "data_source": EvalRun.DataSource.DATASET,
        "dataset": str(dataset.id),
        "cell": str(cell.id),
        "max_items": payload.max_items,
        "sampling": payload.sampling,
        "judge_model": payload.judge_model,
        "variants_input": variants,
    }
    if eval_set is not None:
        serializer_data["eval_set"] = str(eval_set.id)
    if len(evaluators) > 0:
        serializer_data["evaluator_ids"] = [str(item.id) for item in evaluators]
    serializer = EvalRunSerializer(
        data=serializer_data, context={"request": SimpleNamespace(user=context.user)}
    )
    try:
        serializer.is_valid(raise_exception=True)
    except DRFValidationError as error:
        raise _serializer_error(error, operation="run") from None
    run = serializer.save(triggered_by=context.user)
    try:
        result = run_eval_run.apply_async(kwargs={"eval_run_id": str(run.id)})
    except Exception as error:
        EvalRun.objects.filter(pk=run.pk).update(
            status=EvalRun.Status.FAILED,
            error="Could not queue the evaluation run. Try again.",
        )
        raise MCPError(
            "evaluation_dispatch_failed",
            "The evaluation run could not be queued. Try again.",
            retryable=True,
        ) from error
    EvalRun.objects.filter(pk=run.pk).update(celery_task_id=result.id)
    run.refresh_from_db()
    run_link = resource_link("eval-runs", str(run.id), run.name)
    dataset_link = resource_link("datasets", str(dataset.id), dataset.name or "Dataset")
    job = EvaluationJobContract(
        id=str(run.id),
        kind="eval_run",
        status=run.status,
        resource=resource_link("jobs", f"eval_run/{run.id}", f"Evaluation run {run.name}"),
    )
    variant_rows = [
        EvaluationVariantSummaryContract(
            id=str(variant.id),
            label=variant.label,
            mode=variant.mode,
            is_baseline=variant.is_baseline,
        )
        for variant in run.variants.order_by("order", "created_at")[:20]
    ]
    return RunEvaluationOutput(
        summary="Evaluation run queued.",
        status=run.status,
        run_id=str(run.id),
        job=job,
        cell=mcp_cell_contract(dataset, run.cell or cell, "eval"),
        variants=variant_rows,
        resource=run_link,
        resource_links=[run_link, job.resource, dataset_link],
    )


def _compare_sync(
    payload: CompareEvaluationsInput, context: MCPContext
) -> CompareEvaluationsOutput:
    current = _resolve_run(context, payload.run)
    baseline = _resolve_run(context, payload.baseline)
    compared = comparison.compare_runs(current.summary or {}, baseline.summary or {})
    rows = list(compared.get("rows") or [])[:100]
    not_applicable = dict(list((compared.get("not_applicable_by_evaluator") or {}).items())[:100])
    data = {
        "summary": "Evaluation comparison ready.",
        "current_run_id": str(current.id),
        "baseline_run_id": str(baseline.id),
        "current_name": current.name,
        "baseline_name": baseline.name,
        "rows": rows,
        "overall": compared.get("overall") or {},
        "trust": compared.get("trust") or {},
        "not_applicable_by_evaluator": safe_json(not_applicable),
        "resource_links": [
            resource_link("eval-runs", str(current.id), current.name),
            resource_link("eval-runs", str(baseline.id), baseline.name),
        ],
    }
    try:
        return CompareEvaluationsOutput.model_validate(data)
    except Exception as error:
        raise MCPError(
            "evaluation_not_ready", "The evaluation comparison is not available."
        ) from error


def _annotation_sync(
    payload: AnnotateEvaluationSampleInput, context: MCPContext
) -> AnnotationOutput:
    current = None
    if payload.annotation:
        normalized = _uuid(payload.annotation)
        current = (
            Annotation.objects.filter(id=normalized, project=context.project)
            .select_related("sample__run", "sample__variant", "evaluator", "user")
            .first()
            if normalized
            else None
        )
        if current is None:
            raise MCPError("annotation_not_found", "The annotation was not found in this project.")

    sample = (
        _resolve_sample(context, payload.sample)
        if payload.sample
        else current.sample
        if current
        else None
    )
    if sample is None:
        raise MCPError("eval_sample_not_found", "An eval sample is required for a new annotation.")
    evaluator = _resolve_evaluator(context, payload.evaluator) if payload.evaluator else None
    data: dict[str, Any] = {}
    if current is None or payload.sample:
        data["sample"] = str(sample.id)
    if payload.evaluator:
        data["evaluator"] = str(evaluator.id) if evaluator else None
    for field in ("value", "label", "note"):
        if field in payload.model_fields_set:
            data[field] = getattr(payload, field)
    serializer = AnnotationSerializer(instance=current, data=data, partial=current is not None)
    try:
        serializer.is_valid(raise_exception=True)
    except DRFValidationError as error:
        raise MCPError("annotation_invalid", "The annotation failed validation.") from error
    annotation = serializer.save(
        user=context.user,
        project=context.project,
    )

    annotation.refresh_from_db()
    run_link = resource_link("eval-runs", str(annotation.sample.run_id), annotation.sample.run.name)
    return AnnotationOutput(
        summary=f"Evaluation annotation {('updated' if current else 'created')}.",
        status="updated" if current else "created",
        id=str(annotation.id),
        sample_id=str(annotation.sample_id),
        evaluator=annotation.evaluator.name if annotation.evaluator_id else None,
        annotated_by=annotation.user.get_username() if annotation.user_id else "",
        value=annotation.value,
        label=annotation.label,
        note=annotation.note,
        resource=run_link,
        resource_links=[run_link],
    )


def _create_eval_set_sync(payload: CreateEvalSetInput, context: MCPContext) -> CreateEvalSetOutput:
    capability = _resolve_capability(context, payload.capability) if payload.capability else None
    evaluators = [_resolve_evaluator(context, str(id_)) for id_ in payload.evaluator_ids]
    serializer = EvalSetSerializer(
        data={
            "name": payload.name,
            "project": str(context.project.id),
            "capability": str(capability.id) if capability else None,
            "evaluator_ids": [str(ev.id) for ev in evaluators],
        },
        context={"request": SimpleNamespace(user=context.user)},
    )
    try:
        serializer.is_valid(raise_exception=True)
        eval_set = serializer.save(created_by=context.user)
    except DRFValidationError as exc:
        raise MCPError(
            "eval_set_invalid",
            "The eval set failed validation.",
            fields={str(key): str(value) for key, value in exc.detail.items()},
        ) from exc
    return CreateEvalSetOutput(
        summary="Eval set created.",
        eval_set=EvalSetReadinessContract(
            id=str(eval_set.id),
            name=eval_set.name,
            capability=str(capability.id) if capability else None,
            active=False,
            member_count=eval_set.members.count(),
        ),
        resource_links=[resource_link("eval-sets", str(eval_set.id), eval_set.name)],
    )


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_evaluation_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "create_eval_set",
            "Create eval set",
            "Create an eval set from project library evaluators in their applicable roles. Capability is optional. Does not activate it or start a run.",
            CreateEvalSetInput,
            CreateEvalSetOutput,
            _create_eval_set_sync,
            False,
            False,
            "free",
            "sync",
            {"overmind:evaluate"},
        ),
        (
            "check_evaluation_readiness",
            "Check evaluation readiness",
            "Inspect dataset, bindings, credits and model/judge context estimates. Context warnings are advisory.",
            CheckEvaluationReadinessInput,
            CheckEvaluationReadinessOutput,
            _readiness_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
        (
            "upsert_evaluator",
            "Upsert evaluator",
            "Create/update a project evaluator. judge_model selects its generative judge. config.decision opts into Jev with confidence fallback after workload validation. Authoring and holistic judgments stay generative.",
            EvaluatorUpsertInput,
            EvaluatorUpsertOutput,
            _upsert_sync,
            False,
            False,
            "compute",
            "sync",
            {"overmind:evaluate"},
        ),
        (
            "run_evaluation",
            "Run evaluation",
            "Launch an eval run. Preview context and costs with check_evaluation_readiness.",
            RunEvaluationInput,
            RunEvaluationOutput,
            _run_sync,
            False,
            False,
            "llm",
            "job",
            {"overmind:evaluate"},
        ),
        (
            "compare_evaluations",
            "Compare evaluations",
            "Compare an evaluation run with a project-scoped baseline.",
            CompareEvaluationsInput,
            CompareEvaluationsOutput,
            _compare_sync,
            True,
            True,
            "free",
            "sync",
            {"overmind:read"},
        ),
        (
            "annotate_evaluation_sample",
            "Annotate evaluation sample",
            "Create or update a human annotation on one evaluation sample and attribute it to the authenticated user.",
            AnnotateEvaluationSampleInput,
            AnnotationOutput,
            _annotation_sync,
            False,
            False,
            "free",
            "sync",
            {"overmind:evaluate"},
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
                open_world=name == "run_evaluation",
                required_scopes=frozenset(scopes),
                cost_class=cost_class,
                async_mode=async_mode,
            ),
            _async_handler(function),
        )
