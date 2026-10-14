"""Stable, non-sensitive MCP errors."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from mcp import types
from pydantic import Field

from overbae.services.datasets import review
from overbae.services.datasets import use as dataset_use
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.mcp.contracts.common import MCPModel
from overbae.services.mcp.result_compat import tool_result

ErrorCode = Literal[
    "authentication_required",
    "authentication_failed",
    "project_required",
    "permission_denied",
    "invalid_request",
    "invalid_tool",
    "invalid_input",
    "invalid_output",
    "protocol_version_unsupported",
    "origin_not_allowed",
    "resource_not_found",
    "capability_not_found",
    "dataset_not_found",
    "dataset_busy",
    "dataset_invalid",
    "dataset_not_ready",
    "dataset_stopped",
    "version_not_found",
    "run_not_found",
    "attachment_missing",
    "attachment_invalid",
    "import_not_running",
    "import_not_blocked",
    "request_key_conflict",
    "request_key_pending",
    "no_traces",
    "no_calls",
    "query_invalid",
    "dataset_intent_mismatch",
    "dataset_locked",
    "active_conflict",
    "query_timeout",
    "query_result_too_large",
    "eval_set_not_found",
    "eval_set_invalid",
    "invalid_judge_model",
    "preparation_invalid",
    "benchmark_model_invalid",
    "evaluator_not_found",
    "evaluator_invalid",
    "evaluation_not_ready",
    "evaluation_dispatch_failed",
    "eval_run_not_found",
    "eval_sample_not_found",
    "annotation_not_found",
    "annotation_invalid",
    "invalid_trace_selection",
    "no_failures_found",
    "insufficient_credits",
    "plan_limit_exceeded",
    "model_not_found",
    "model_warming",
    "model_unavailable",
    "finetune_not_found",
    "finetune_invalid",
    "finetune_not_ready",
    "finetune_dispatch_failed",
    "optimizer_not_found",
    "optimizer_not_ready",
    "optimizer_invalid",
    "optimizer_pr_not_ready",
    "deployment_not_found",
    "deployment_invalid",
    "deployment_not_ready",
    "deployment_dispatch_failed",
    "connector_not_found",
    "connector_unsupported",
    "connector_setup_required",
    "connector_mapping_invalid",
    "connector_provider_error",
    "connector_sync_dispatch_failed",
    "instrumentation_plan_not_found",
    "active_model_invalid",
    "inference_failed",
    "decision_job_not_found",
    "context_length_exceeded",
    "model_swap_prompt_not_ready",
    "insight_not_found",
    "insight_not_open",
    "insight_has_no_fix",
    "invalid_stage_operation",
    "staged_change_active",
    "staged_change_not_found",
    "staged_change_stale",
    "staged_change_conflict",
    "invalid_filter",
    "embedding_unavailable",
    "internal_error",
]


class MCPErrorData(MCPModel):
    code: str
    message: str
    retryable: bool = False
    fields: dict[str, str] = Field(default_factory=dict)
    retry_after_seconds: int | None = Field(default=None, exclude_if=lambda value: value is None)


class MCPError(Exception):
    """An error safe to return to an MCP client."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        fields: dict[str, str] | None = None,
        retry_after_seconds: int | None = None,
    ) -> None:
        self.data = MCPErrorData(
            code=code,
            message=message,
            retryable=retryable,
            fields=fields or {},
            retry_after_seconds=retry_after_seconds,
        )
        super().__init__(message)


def error_result(error: MCPError) -> types.CallToolResult:
    return tool_result({"error": error.data.model_dump(mode="json")}, is_error=True)


def internal_error() -> MCPError:
    return MCPError("internal_error", "The MCP request could not be completed.")


def error_payload(error: MCPError) -> dict[str, Any]:
    return error.data.model_dump(mode="json")


DATASET_ERROR_CODES: dict[str, ErrorCode] = {
    "intent": "dataset_intent_mismatch",
    "landing": "dataset_busy",
    "land_stopped": "dataset_stopped",
    "no_cell": "version_not_found",
    "no_version": "version_not_found",
    "cell_mismatch": "version_not_found",
    "no_execution": "run_not_found",
    "frozen": "dataset_locked",
    "active_conflict": "active_conflict",
    "attachment_missing": "attachment_missing",
    "attachment_invalid": "attachment_invalid",
    "import_not_running": "import_not_running",
    "import_not_blocked": "import_not_blocked",
}


def dataset_mcp_error(error) -> MCPError:
    """Translate ``DatasetError``; do not re-check contracts here."""
    code = DATASET_ERROR_CODES.get(getattr(error, "code", ""), "dataset_invalid")
    message = str(getattr(error, "detail", None) or error)
    field = getattr(error, "field", "")
    fields = {field: message[:300]} if field else None
    if code == "dataset_busy":
        return MCPError(
            code,
            f"{message} Poll get_job with kind dataset_import until it is idle.",
            retryable=True,
            fields=fields,
        )
    if code == "dataset_locked":
        message += (
            " To change it, land the version as a new dataset with create_dataset "
            "(source kind version)."
        )
    return MCPError(code, message, fields=fields, retryable=code == "attachment_missing")


_DATASET_URI = "overmind://datasets/"


def mcp_dataset(context, reference: str):
    """A project dataset by id, resource URI, or name. Names are not unique, so
    a name that matches two datasets is refused rather than guessed."""
    from overbae.models import Dataset

    value = str(reference).strip().removeprefix(_DATASET_URI)
    query = Dataset.objects.filter(project=context.project).select_related("capability", "active")
    try:
        dataset = query.filter(id=uuid.UUID(value)).first()
    except ValueError:
        matches = list(query.filter(name__iexact=value).order_by("-created_at")[:2])
        if len(matches) > 1:
            raise MCPError(
                "dataset_not_found", "Several datasets have this name; use the dataset id."
            ) from None
        dataset = matches[0] if matches else None
    if dataset is None:
        raise MCPError("dataset_not_found", "The dataset was not found in this project.")
    return dataset


def mcp_version(dataset, ref: str | None, *, field: str = "version"):
    """Optional version; blank means the caller should use the active one."""
    from overbae.services.datasets.versions import resolve_cell

    if not ref:
        return None
    try:
        return resolve_cell(dataset, ref, field=field)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc


def mcp_check(dataset, intent: str, ref: str | None = None, *, field: str = "version"):
    from overbae.services.datasets import use

    try:
        return use.check(dataset, intent, cell=mcp_version(dataset, ref, field=field))
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc


def version_contract(dataset, cell, intent: str):
    from overbae.services.mcp.contracts.common import DatasetVersionContract

    if cell is None:
        return None
    fits, reason = cell.fits(intent)
    if fits:
        try:
            dataset_use.check(dataset, intent, cell=cell)
        except DatasetError as exc:
            fits, reason = False, exc.detail
    return DatasetVersionContract(
        id=str(cell.id),
        version=cell.label,
        title=cell.title,
        rows=int(cell.rows or 0),
        fingerprint=cell.fingerprint or "",
        fits=fits,
        reason="" if fits else str(reason)[:500],
        warnings=review.warnings(dataset, cell),
    )


def pinned_version(cell):
    """A version a job or run read, as every launch output and resource names it."""
    from overbae.services.mcp.contracts.common import PinnedVersion

    if cell is None:
        return None
    return PinnedVersion(
        dataset=str(cell.dataset_id),
        version=cell.label,
        id=str(cell.id),
        fingerprint=cell.fingerprint or "",
    )


def pinned_versions(*, train=None, validation=None, eval=None):
    from overbae.services.mcp.contracts.common import PinnedVersions

    return PinnedVersions(
        train=pinned_version(train),
        validation=pinned_version(validation),
        eval=pinned_version(eval),
    )


def finetune_versions(job):
    return pinned_versions(train=job.cell, validation=job.validation_cell, eval=job.eval_cell)
