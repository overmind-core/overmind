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
    "cell_not_found",
    "query_invalid",
    "dataset_build_not_found",
    "dataset_build_invalid",
    "dataset_build_conflict",
    "dataset_intent_mismatch",
    "dataset_locked",
    "eval_set_not_found",
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


class MCPError(Exception):
    """An error safe to return to an MCP client."""

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        retryable: bool = False,
        fields: dict[str, str] | None = None,
    ) -> None:
        self.data = MCPErrorData(
            code=code,
            message=message,
            retryable=retryable,
            fields=fields or {},
        )
        super().__init__(message)


def error_result(error: MCPError) -> types.CallToolResult:
    return tool_result({"error": error.data.model_dump(mode="json")}, is_error=True)


def internal_error() -> MCPError:
    return MCPError("internal_error", "The MCP request could not be completed.")


def error_payload(error: MCPError) -> dict[str, Any]:
    return error.data.model_dump(mode="json")


_DATASET_ERROR_CODES: dict[str, ErrorCode] = {
    "intent": "dataset_intent_mismatch",
    "running": "dataset_busy",
    "landing": "dataset_busy",
    "diagnosing": "dataset_busy",
    "no_cell": "cell_not_found",
    "cell_mismatch": "cell_not_found",
}


def dataset_mcp_error(error) -> MCPError:
    """Translate ``DatasetError``; do not re-check contracts here."""
    code = _DATASET_ERROR_CODES.get(getattr(error, "code", ""), "dataset_invalid")
    message = str(getattr(error, "detail", None) or error)
    if code == "dataset_busy":
        return MCPError(
            code, f"{message} Poll get_job with kind dataset_run until it is idle.", retryable=True
        )
    return MCPError(code, message)


_DATASET_URI = "overmind://datasets/"


def mcp_dataset(context, reference: str):
    """A project dataset by id, resource URI, or name. Names are not unique, so
    a name that matches two datasets is refused rather than guessed."""
    from overbae.models import Dataset

    value = str(reference).strip().removeprefix(_DATASET_URI)
    query = (
        Dataset.objects.filter(project=context.project)
        .select_related("capability", "active")
        .prefetch_related("cells")
    )
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


def mcp_cell(dataset, ref: str | None):
    """Optional cell/version; blank means the caller should use the active cell."""
    from overbae.services.datasets.lifecycle import DatasetError
    from overbae.services.datasets.notebook.agent import resolve_cell

    if not ref:
        return None
    try:
        return resolve_cell(dataset, ref, ran_only=True)
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc


def mcp_check(dataset, intent: str, ref: str | None = None):
    from overbae.services.datasets import use
    from overbae.services.datasets.lifecycle import DatasetError

    try:
        return use.check(dataset, intent, cell=mcp_cell(dataset, ref))
    except DatasetError as exc:
        raise dataset_mcp_error(exc) from exc


def mcp_cell_contract(dataset, cell, intent: str):
    from overbae.services.mcp.contracts.common import DatasetCellContract

    if cell is None:
        return None
    fits, reason = cell.fits(intent)
    if fits:
        try:
            dataset_use.check(dataset, intent, cell=cell)
        except DatasetError as exc:
            fits, reason = False, exc.detail
    return DatasetCellContract(
        id=str(cell.id),
        version=dataset.versions().get(cell.id, ""),
        title=cell.title,
        rows=int(cell.rows or 0),
        fingerprint=cell.fingerprint or "",
        fits=fits,
        reason="" if fits else str(reason)[:500],
        warnings=review.warnings(dataset, cell),
    )
