"""Decision models over MCP: the catalog, training jobs and System One requests."""

from __future__ import annotations

import time
import uuid

from asgiref.sync import sync_to_async

from overbae.core.errors import InputValidationError
from overbae.models import DecisionJob
from overbae.services import decision_jobs, decision_recommend
from overbae.services.datasets import rows
from overbae.services.datasets import use as dataset_use
from overbae.services.datasets.contract import public_intent
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.datasets.rows import RowStoreError
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.common import VersionRef
from overbae.services.mcp.contracts.decisions import (
    CheckDecisionReadinessInput,
    CheckDecisionReadinessOutput,
    DecisionCandidate,
    DecisionDatasetReference,
    DecisionJobContract,
    DecisionPick,
    DecisionTraining,
    DecisionUsage,
    RunDecisionModelInput,
    RunDecisionModelOutput,
    StartDecisionJobInput,
    StartDecisionJobOutput,
)
from overbae.services.mcp.contracts.finetuning import FineTuneCreditReadiness
from overbae.services.mcp.errors import (
    MCPError,
    dataset_mcp_error,
    mcp_dataset,
    mcp_version,
    pinned_versions,
)
from overbae.services.mcp.resources import resource_link

_DECISION_MODELS_LINK = {
    "uri": "overmind://decision-models",
    "title": "Decision models",
    "mimeType": "application/json",
}


def _training(result: dict) -> DecisionTraining | None:
    # Clef trains on rows; D3 and d1 train one example per question.
    if "train_records" in result:
        examples, unit, skipped = result["train_records"], "rows", result.get("skipped")
    elif "train_questions" in result:
        examples, unit, skipped = (
            result["train_questions"],
            "questions",
            result.get("skipped_questions"),
        )
    else:
        return None
    return DecisionTraining(
        examples=examples,
        unit=unit,
        holdout=result.get("holdout_records"),
        skipped=skipped if isinstance(skipped, int) else None,
        steps=result.get("steps"),
        calibration=result.get("calibration") or None,
    )


def decision_job_contract(job: DecisionJob) -> DecisionJobContract:
    result = job.result if isinstance(job.result, dict) else {}
    return DecisionJobContract(
        id=str(job.id),
        model=job.model_id,
        status=job.status,
        versions=pinned_versions(train=job.cell, eval=job.eval_cell),
        error=job.error or None,
        evaluation=job.evaluation or {},
        evaluation_status=job.evaluation_status,
        checkpoint_ready=job.status == DecisionJob.Status.SUCCEEDED and bool(result.get("saved")),
        loss=result.get("loss_first_last") or [],
        training=_training(result),
        cost_usd=str(job.cost_usd) if job.cost_usd is not None else None,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
        resource=resource_link("jobs", f"decision_job/{job.id}", f"Decision job {job.model_id}"),
    )


def project_job(context: MCPContext, reference: str) -> DecisionJob:
    try:
        job_id = uuid.UUID(str(reference))
    except ValueError:
        job_id = None
    job = (
        DecisionJob.objects.select_related("cell", "eval_cell")
        .filter(project=context.project, pk=job_id)
        .first()
        if job_id
        else None
    )
    if job is None:
        raise MCPError("decision_job_not_found", "The decision job was not found in this project.")
    return job


def _require_credits(context: MCPContext) -> None:
    from overbae.api.credit_gate import PaymentRequired, require_credits

    try:
        require_credits(context.user)
    except PaymentRequired as error:
        raise MCPError(
            "insufficient_credits", "This operation requires available credits."
        ) from error


def _credits_available(context: MCPContext) -> bool:
    try:
        _require_credits(context)
    except MCPError:
        return False
    return True


def _input_error(error: InputValidationError) -> MCPError:
    code = (
        "model_not_found"
        if str(error.detail).startswith("Unknown decision model")
        else "invalid_request"
    )
    return MCPError(code, error.detail)


def _start(payload: StartDecisionJobInput, context: MCPContext) -> StartDecisionJobOutput:
    _require_credits(context)
    dataset = mcp_dataset(context, payload.dataset)
    eval_dataset = mcp_dataset(context, payload.eval_dataset) if payload.eval_dataset else None
    try:
        job = decision_jobs.create(
            context.project,
            context.user,
            payload.model,
            dataset,
            version=payload.version,
            eval_dataset=eval_dataset,
            eval_version=payload.eval_version,
        )
    except InputValidationError as exc:
        raise _input_error(exc) from exc
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    contract = decision_job_contract(project_job(context, str(job.id)))
    return StartDecisionJobOutput(
        summary=f"Decision job queued on {contract.model}. Poll get_job with kind decision_job.",
        job=contract,
        resource_links=[contract.resource],
    )


def _run(payload: RunDecisionModelInput, context: MCPContext) -> RunDecisionModelOutput:
    if (payload.model is None) == (payload.job is None):
        raise MCPError("invalid_request", "Give exactly one of model or job.")
    job = project_job(context, payload.job) if payload.job is not None else None
    _require_credits(context)
    started = time.monotonic()
    try:
        answered = decision_jobs.answer(
            context.project,
            context.user,
            job.model_id if job is not None else payload.model,
            payload.state,
            payload.questions,
            job=job,
        )
    except InputValidationError as exc:
        raise _input_error(exc) from exc
    except decision_jobs.DecisionWarmingError as exc:
        raise MCPError(
            "model_warming",
            "The decision model is starting. Retry after the hinted delay.",
            retryable=True,
            retry_after_seconds=decision_jobs.RETRY_AFTER_SECONDS,
        ) from exc
    except decision_jobs.DecisionUnavailableError as exc:
        raise MCPError(
            "model_unavailable",
            "The decision model could not answer.",
            retryable=True,
            fields={"reason": exc.reason},
        ) from exc
    latency_ms = round((time.monotonic() - started) * 1000, 3)
    return RunDecisionModelOutput(
        summary=f"Decision answered by {answered['model']}.",
        model=answered["model"],
        answers=answered["answers"],
        usage=DecisionUsage(**answered["usage"]),
        latency_ms=latency_ms,
        is_cold=False,
        resource=decision_job_contract(job).resource if job is not None else _DECISION_MODELS_LINK,
    )


def _candidate(row: dict) -> DecisionCandidate:
    return DecisionCandidate(**row)


def _ref(cell) -> VersionRef | None:
    return VersionRef(id=str(cell.id), version=cell.label) if cell is not None else None


def _decision_warnings(cell) -> list[str]:
    return ((cell.intent_report or {}).get("decision") or {}).get("warnings") or []


def _readiness(
    payload: CheckDecisionReadinessInput, context: MCPContext
) -> CheckDecisionReadinessOutput:
    dataset = mcp_dataset(context, payload.dataset)
    cell = mcp_version(dataset, payload.version) or dataset.active_cell
    missing: list[str] = []
    warnings: list[str] = []
    train_ok = False
    if cell is None:
        missing.append("training dataset — The dataset has no version yet.")
    else:
        try:
            dataset_use.check(dataset, "train", cell=cell, decision=True)
            train_ok = True
        except DatasetError as exc:
            missing.append(f"training dataset — {exc.detail}")
        warnings += [f"training dataset: {finding}" for finding in _decision_warnings(cell)]

    eval_dataset = mcp_dataset(context, payload.eval_dataset) if payload.eval_dataset else None
    eval_cell = eval_ok = None
    if eval_dataset is not None:
        eval_cell = (
            mcp_version(eval_dataset, payload.eval_version, field="eval_version")
            or eval_dataset.active_cell
        )
        if eval_cell is None:
            missing.append("eval dataset — The dataset has no version yet.")
        else:
            try:
                dataset_use.check(eval_dataset, "eval", cell=eval_cell, decision=True)
                eval_ok = eval_cell
            except DatasetError as exc:
                missing.append(f"eval dataset — {exc.detail}")
            warnings += [f"eval dataset: {finding}" for finding in _decision_warnings(eval_cell)]

    report = None
    if train_ok:
        try:
            report = decision_recommend.recommend(dataset, str(cell.id), eval_cell=eval_ok)
            overlap = rows.contamination(cell, eval_ok)["overlap_count"] if eval_ok else 0
        except RowStoreError as exc:
            missing.append(f"training dataset — {exc}")
        else:
            if overlap:
                warnings.append(
                    f"train/eval split — {overlap} overlapping training rows; review the split before training"
                )
    if report is not None and not report["picks"]:
        missing.append("model — No model fits.")

    credits_available = _credits_available(context)
    if not credits_available:
        missing.append("credits — add credits before starting decision training")
    candidates = report["candidates"] if report is not None else []
    links = [resource_link("datasets", str(dataset.id), dataset.name or "Dataset")]
    if eval_dataset is not None:
        links.append(
            resource_link("datasets", str(eval_dataset.id), eval_dataset.name or "Eval dataset")
        )
    ready = not missing
    return CheckDecisionReadinessOutput(
        summary="Decision training is ready." if ready else "Decision training is not ready.",
        ready=ready,
        missing=missing,
        warnings=warnings,
        dataset=DecisionDatasetReference(
            id=str(dataset.id),
            name=dataset.name or str(dataset.id)[:8],
            intent=public_intent(dataset.intent),
            version=_ref(cell),
        ),
        eval_dataset=(
            DecisionDatasetReference(
                id=str(eval_dataset.id),
                name=eval_dataset.name or str(eval_dataset.id)[:8],
                intent=public_intent(eval_dataset.intent),
                version=_ref(eval_cell),
            )
            if eval_dataset is not None
            else None
        ),
        picks=[DecisionPick(**pick) for pick in report["picks"]] if report is not None else [],
        recommendations=[_candidate(row) for row in candidates if row["fits"]],
        excluded=[_candidate(row) for row in candidates if not row["fits"]],
        credits=FineTuneCreditReadiness(required=True, available=credits_available),
        resource_links=links,
    )


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_decision_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    for definition, handler in (
        (
            ToolDefinition(
                name="check_decision_readiness",
                title="Check decision readiness",
                description="Check decision rows and an optional eval set: fitting and excluded models, warnings, credits.",
                input_model=CheckDecisionReadinessInput,
                output_model=CheckDecisionReadinessOutput,
                read_only=True,
                idempotent=True,
                open_world=False,
                required_scopes=frozenset({"overmind:read"}),
                cost_class="free",
                async_mode="sync",
            ),
            _async_handler(_readiness),
        ),
        (
            ToolDefinition(
                name="start_decision_job",
                title="Start decision job",
                description="Train a decision model on decision rows (overmind://decision-models). Pass version and eval_version; the job pins them.",
                input_model=StartDecisionJobInput,
                output_model=StartDecisionJobOutput,
                read_only=False,
                idempotent=False,
                open_world=False,
                required_scopes=frozenset({"overmind:train"}),
                cost_class="gpu",
                async_mode="job",
            ),
            _async_handler(_start),
        ),
        (
            ToolDefinition(
                name="run_decision_model",
                title="Run decision model",
                description="Answer a System One request from a catalog model or a trained job (exactly one). Returns option probabilities; retry after retry_after_seconds on model_warming.",
                input_model=RunDecisionModelInput,
                output_model=RunDecisionModelOutput,
                read_only=False,
                idempotent=False,
                open_world=True,
                required_scopes=frozenset({"overmind:deploy"}),
                cost_class="llm",
                async_mode="sync",
            ),
            _async_handler(_run),
        ),
    ):
        catalog.register(definition, handler)
