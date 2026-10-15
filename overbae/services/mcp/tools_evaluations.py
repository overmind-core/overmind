"""Project-scoped MCP tools for the eval matrix, eval sets and evaluation runs."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

from asgiref.sync import sync_to_async
from django.db.models import Q
from rest_framework.exceptions import ValidationError as DRFValidationError

from overbae.api.credit_gate import PaymentRequired, require_credits
from overbae.api.eval_serializers import (
    EvalMatrixSerializer,
    EvalRunCreateSerializer,
    EvalSetSerializer,
    EvaluationContextRequestSerializer,
)
from overbae.innermind import comparison, runs
from overbae.innermind.definitions import definition_source
from overbae.models import Capability, Dataset, EvalMatrix, EvalRun, EvalSet
from overbae.services.datasets.contract import public_intent
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.evaluations import (
    CheckEvaluationReadinessInput,
    CheckEvaluationReadinessOutput,
    CompareEvaluationsInput,
    CompareEvaluationsOutput,
    CreateEvalMatrixRowInput,
    CreateEvalMatrixRowOutput,
    CreateEvalSetInput,
    CreateEvalSetOutput,
    CreditReadinessContract,
    DeleteEvalMatrixRowInput,
    DeleteEvalMatrixRowOutput,
    EvalMatrixRowContract,
    EvalSetContract,
    EvaluationDatasetContract,
    EvaluationRunContract,
    EvaluationScopeInput,
    RunEvaluationInput,
    RunEvaluationOutput,
)
from overbae.services.mcp.errors import MCPError, mcp_cell, mcp_cell_contract, mcp_dataset
from overbae.services.mcp.resources import resource_link


def _uuid(value: str) -> str | None:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError):
        return None


def _request(context: MCPContext) -> dict:
    return {"request": SimpleNamespace(user=context.user)}


def _invalid(code: str, message: str, error: DRFValidationError) -> MCPError:
    detail = error.detail if isinstance(error.detail, dict) else {"detail": error.detail}
    fields = {str(key): str(value)[:240] for key, value in detail.items()}
    first = next(iter(fields.values()), "")
    return MCPError(code, f"{message} {first}".strip()[:500], fields=fields)


def _capability(context: MCPContext, reference: str) -> Capability:
    query = Capability.objects.filter(project=context.project).exclude(status="deleted")
    normalized = _uuid(reference)
    capability = query.filter(id=normalized).first() if normalized else None
    if capability is None:
        capability = (
            query.filter(slug__iexact=reference).first()
            or query.filter(name__iexact=reference).first()
        )
    if capability is None:
        raise MCPError("capability_not_found", "The capability was not found in this project.")
    return capability


def _matrix_row(context: MCPContext, reference: str, capability: Capability | None = None):
    query = EvalMatrix.objects.filter(project=context.project)
    normalized = _uuid(reference)
    if normalized:
        row = query.filter(id=normalized).first()
    else:
        scoped = query.filter(name__iexact=reference)
        if capability is not None:
            scoped = scoped.filter(Q(capability=capability) | Q(capability__isnull=True))
        matches = list(scoped[:2])
        if len(matches) > 1:
            raise MCPError("eval_matrix_row_not_found", "Multiple rows match; use the row id.")
        row = matches[0] if matches else None
    if row is None:
        raise MCPError("eval_matrix_row_not_found", f"No eval matrix row {reference!r}.")
    return row


def _eval_set(context: MCPContext, reference: str) -> EvalSet:
    query = EvalSet.objects.filter(project=context.project).select_related("capability")
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


def _run(context: MCPContext, reference: str) -> EvalRun:
    query = EvalRun.objects.filter(project=context.project).select_related("model_ref")
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


def _eval_dataset(context: MCPContext, reference: str) -> Dataset:
    dataset = mcp_dataset(context, reference)
    intent = public_intent(dataset.intent)
    if intent != Dataset.Intent.EVAL:
        raise MCPError(
            "dataset_intent_mismatch",
            f"Evaluation requires an eval dataset; this dataset has intent={intent}.",
        )
    return dataset


def _set_contract(eval_set: EvalSet) -> EvalSetContract:
    return EvalSetContract(
        id=str(eval_set.id),
        name=eval_set.name,
        capability=eval_set.capability.slug if eval_set.capability_id else None,
        active=bool(
            eval_set.capability_id and eval_set.capability.active_eval_set_id == eval_set.id
        ),
        row_count=eval_set.rows.count(),
    )


def _scope_data(payload: EvaluationScopeInput, context: MCPContext, dataset: Dataset) -> dict:
    cell = mcp_cell(dataset, payload.cell or payload.version)
    data: dict[str, Any] = {
        "project": str(context.project.id),
        "dataset": str(dataset.id),
        "judge_model": payload.judge_model,
        "targets": [target.model_dump(exclude_none=True) for target in payload.targets],
    }
    if cell is not None:
        data["cell"] = str(cell.id)
    if payload.eval_set:
        data["eval_set"] = str(_eval_set(context, payload.eval_set).id)
    return data


def _credits_available(user) -> bool:
    try:
        require_credits(user)
    except PaymentRequired:
        return False
    return True


def _row_contract(row: EvalMatrix) -> EvalMatrixRowContract:
    return EvalMatrixRowContract(
        id=str(row.id),
        name=row.name,
        type=row.type,
        source=row.source,
        definition_source=definition_source(row),
        capability=row.capability.slug if row.capability_id else None,
        span_type=row.span_type,
        span_name=row.span_name,
        enabled=row.enabled,
    )


def _create_row_sync(
    payload: CreateEvalMatrixRowInput, context: MCPContext
) -> CreateEvalMatrixRowOutput:
    capability = _capability(context, payload.capability) if payload.capability else None
    serializer = EvalMatrixSerializer(
        data={
            "project": str(context.project.id),
            "capability": str(capability.id) if capability else None,
            "name": payload.name,
            "type": payload.type,
            "source": payload.source,
            "span_type": payload.span_type,
            "span_name": payload.span_name,
        },
        context=_request(context),
    )
    try:
        serializer.is_valid(raise_exception=True)
    except DRFValidationError as error:
        raise _invalid("eval_matrix_row_invalid", "The row failed validation.", error) from None
    row = serializer.save(created_by=context.user)
    links = (
        [resource_link("capabilities", str(row.capability_id), row.capability.name)]
        if row.capability_id
        else []
    )
    return CreateEvalMatrixRowOutput(
        summary=f"Eval matrix row {row.name} created.",
        row=_row_contract(row),
        resource_links=links,
    )


def _delete_row_sync(
    payload: DeleteEvalMatrixRowInput, context: MCPContext
) -> DeleteEvalMatrixRowOutput:
    capability = _capability(context, payload.capability) if payload.capability else None
    row = _matrix_row(context, payload.row, capability)
    contract = _row_contract(row)
    row.delete()
    return DeleteEvalMatrixRowOutput(
        summary=f"Eval matrix row {contract.name} deleted.", row=contract
    )


def _create_eval_set_sync(payload: CreateEvalSetInput, context: MCPContext) -> CreateEvalSetOutput:
    capability = _capability(context, payload.capability) if payload.capability else None
    rows = [_matrix_row(context, ref, capability) for ref in payload.rows]
    serializer = EvalSetSerializer(
        data={
            "name": payload.name,
            "project": str(context.project.id),
            "capability": str(capability.id) if capability else None,
            "rows": [str(row.id) for row in rows],
        },
        context=_request(context),
    )
    try:
        serializer.is_valid(raise_exception=True)
    except DRFValidationError as error:
        raise _invalid("eval_set_invalid", "The eval set failed validation.", error) from None
    eval_set = serializer.save(created_by=context.user)
    if payload.activate:
        if capability is None:
            raise MCPError("eval_set_invalid", "Only a capability's set can be activated.")
        capability.active_eval_set = eval_set
        capability.save(update_fields=["active_eval_set"])
    eval_set.refresh_from_db()
    return CreateEvalSetOutput(
        summary=f"Eval set {eval_set.name} created with {len(rows)} rows.",
        eval_set=_set_contract(eval_set),
        resource_links=[resource_link("eval-sets", str(eval_set.id), eval_set.name)],
    )


def _readiness_sync(
    payload: CheckEvaluationReadinessInput, context: MCPContext
) -> CheckEvaluationReadinessOutput:
    dataset = _eval_dataset(context, payload.dataset)
    serializer = EvaluationContextRequestSerializer(
        data=_scope_data(payload, context, dataset), context=_request(context)
    )
    try:
        serializer.is_valid(raise_exception=True)
    except DRFValidationError as error:
        raise _invalid(
            "evaluation_not_ready", "The evaluation request is invalid.", error
        ) from None
    report = serializer.report()
    matrix = serializer.matrix()
    eval_set = serializer.validated_data.get("eval_set")
    cell = serializer.validated_data.get("cell") or dataset.active_cell
    dataset_data = EvaluationDatasetContract(
        id=str(dataset.id),
        name=dataset.name,
        intent=public_intent(dataset.intent),
        cell=mcp_cell_contract(dataset, cell, "eval"),
    )
    if cell and dataset_data.cell and any(target.mode == "generate" for target in payload.targets):
        from overbae.services.datasets.contract import evaluation_generation_error

        if reason := evaluation_generation_error(cell.intent_report):
            dataset_data.cell.fits = False
            dataset_data.cell.reason = reason
    credits = CreditReadinessContract(required=True, available=_credits_available(context.user))
    ready = bool(matrix and credits.available and dataset_data.cell and dataset_data.cell.fits)
    links = [resource_link("datasets", str(dataset.id), dataset.name or "Dataset")]
    if eval_set is not None:
        links.append(resource_link("eval-sets", str(eval_set.id), eval_set.name))
    return CheckEvaluationReadinessOutput(
        summary="Evaluation is ready." if ready else "Evaluation is not ready.",
        ready=ready,
        context_checks=report["checks"],
        judge_models=report["judge_models"],
        dataset=dataset_data,
        eval_set=_set_contract(eval_set) if eval_set is not None else None,
        matrix=[{"name": row["name"], "type": row["type"]} for row in matrix[:200]],
        credits=credits,
        resource_links=links,
    )


def _run_sync(payload: RunEvaluationInput, context: MCPContext) -> RunEvaluationOutput:
    dataset = _eval_dataset(context, payload.dataset)
    data = {
        **_scope_data(payload, context, dataset),
        "name": payload.name,
        "max_items": payload.max_items,
        "sampling": payload.sampling,
    }
    serializer = EvalRunCreateSerializer(data=data, context=_request(context))
    try:
        serializer.is_valid(raise_exception=True)
    except DRFValidationError as error:
        raise _invalid(
            "evaluation_not_ready", "The evaluation request is invalid.", error
        ) from None
    try:
        require_credits(context.user)
    except PaymentRequired as error:
        raise MCPError(
            "insufficient_credits", "Evaluation runs require available credits."
        ) from error
    try:
        created = serializer.save(triggered_by=context.user)
    except (runs.RunError, DRFValidationError) as error:
        raise MCPError("evaluation_not_ready", str(error)[:500]) from None
    rows = []
    links = []
    for run in created["runs"]:
        run.refresh_from_db()
        resource = resource_link("eval-runs", str(run.id), run.name)
        job = resource_link("jobs", f"eval_run/{run.id}", f"Evaluation run {run.name}")
        rows.append(
            EvaluationRunContract(
                id=str(run.id),
                label=run.label,
                mode=run.mode,
                is_baseline=run.is_baseline,
                status=run.status,
                job=job,
                resource=resource,
            )
        )
        links += [resource, job]
    links.append(resource_link("datasets", str(dataset.id), dataset.name or "Dataset"))
    return RunEvaluationOutput(
        summary=f"{len(rows)} evaluation run{'s' if len(rows) != 1 else ''} queued.",
        group=str(created["group"]),
        runs=rows,
        cell=mcp_cell_contract(dataset, serializer.validated_data["cell"], "eval"),
        resource_links=links,
    )


def _compare_sync(
    payload: CompareEvaluationsInput, context: MCPContext
) -> CompareEvaluationsOutput:
    candidate = _run(context, payload.run)
    baseline = (
        _run(context, payload.baseline)
        if payload.baseline
        else comparison.baseline_of(candidate.group)
    )
    if baseline is None or baseline.pk == candidate.pk:
        raise MCPError(
            "evaluation_not_ready", "No completed baseline to compare with; pass baseline."
        )
    compared = comparison.compare(baseline, candidate)
    overall = compared["overall"]
    delta = f"{overall['delta']:+.3f}" if overall["delta"] is not None else "n/a"
    return CompareEvaluationsOutput.model_validate(
        {
            **compared,
            "metrics": compared["metrics"][:200],
            "summary": f"{candidate.label} vs {baseline.label}: {overall['status']} ({delta}).",
            "resource_links": [
                resource_link("eval-runs", str(baseline.id), baseline.name),
                resource_link("eval-runs", str(candidate.id), candidate.name),
            ],
        }
    )


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_evaluation_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    definitions = [
        (
            "create_eval_matrix_row",
            "Create eval matrix row",
            "Create a deterministic check or an llm expectation. Enabled rows score "
            "every matching ingested span live and every evaluation run. Rows cannot be "
            "edited: delete and create again.",
            CreateEvalMatrixRowInput,
            CreateEvalMatrixRowOutput,
            _create_row_sync,
            False,
            False,
            "free",
            "sync",
            {"overmind:evaluate"},
        ),
        (
            "delete_eval_matrix_row",
            "Delete eval matrix row",
            "Permanently delete one eval matrix row and drop it from every eval set. "
            "Spans already scored and past runs keep their results.",
            DeleteEvalMatrixRowInput,
            DeleteEvalMatrixRowOutput,
            _delete_row_sync,
            False,
            False,
            "free",
            "sync",
            {"overmind:evaluate"},
        ),
        (
            "create_eval_set",
            "Create eval set",
            "Group eval matrix rows into a named set, optionally the capability's active set. "
            "Does not start a run.",
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
            "Inspect the dataset version, the matrix rows a run would freeze, credits and "
            "model/judge context estimates. Context warnings are advisory.",
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
            "run_evaluation",
            "Run evaluation",
            "Launch one evaluation run per target in a shared group. Preview with "
            "check_evaluation_readiness; poll each job resource.",
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
            "Compare a run with its group's baseline, or a named baseline, over the rows both scored.",
            CompareEvaluationsInput,
            CompareEvaluationsOutput,
            _compare_sync,
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
                destructive=name == "delete_eval_matrix_row",
                idempotent=idempotent,
                open_world=name == "run_evaluation",
                required_scopes=frozenset(scopes),
                cost_class=cost_class,
                async_mode=async_mode,
            ),
            _async_handler(function),
        )
