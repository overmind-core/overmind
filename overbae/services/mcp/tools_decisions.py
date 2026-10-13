"""Decision models over MCP: the catalog, training jobs and System One requests."""

from __future__ import annotations

import uuid

from asgiref.sync import sync_to_async

from overbae.core.errors import InputValidationError
from overbae.models import DecisionJob
from overbae.services import decision_jobs
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.decisions import (
    DecisionJobContract,
    StartDecisionJobInput,
    StartDecisionJobOutput,
)
from overbae.services.mcp.errors import MCPError, dataset_mcp_error, mcp_dataset
from overbae.services.mcp.resources import resource_link


def decision_job_contract(job: DecisionJob) -> DecisionJobContract:
    return DecisionJobContract(
        id=str(job.id),
        model=job.model_id,
        status=job.status,
        dataset=str(job.cell.dataset_id),
        version=job.cell.label,
        eval_dataset=str(job.eval_cell.dataset_id) if job.eval_cell_id else None,
        eval_version=job.eval_cell.label if job.eval_cell_id else None,
        error=job.error or None,
        evaluation=job.evaluation or {},
        cost_usd=str(job.cost_usd) if job.cost_usd is not None else None,
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
            eval_dataset=eval_dataset,
        )
    except InputValidationError as exc:
        raise MCPError("model_not_found", exc.detail) from exc
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc
    contract = decision_job_contract(project_job(context, str(job.id)))
    return StartDecisionJobOutput(
        summary=f"Decision job queued on {contract.model}. Poll get_job with kind decision_job.",
        job=contract,
        resource_links=[contract.resource],
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
                name="start_decision_job",
                title="Start decision job",
                description="Train a decision model on decision rows (overmind://decision-models).",
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
    ):
        catalog.register(definition, handler)
