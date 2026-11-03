"""Project-scoped MCP tools for repeating issues and the signals to train that they raise."""

from __future__ import annotations

import uuid

from asgiref.sync import sync_to_async

from overbae.models import Issue
from overbae.services.entity_resolution import resolve_capability
from overbae.services.issue_overview import (
    change_issue,
    issue_detail,
    issue_page,
    overview,
    training_plan,
    training_signal_rows,
)
from overbae.services.mcp.context import MCPContext
from overbae.services.mcp.contracts.common import PageContract
from overbae.services.mcp.contracts.issues import (
    InspectIssueInput,
    InspectIssueOutput,
    IssueContract,
    ListIssuesInput,
    ListIssuesOutput,
    ListTrainingSignalsInput,
    ListTrainingSignalsOutput,
    NextActionContract,
    TrainingSignalContract,
    UpdateIssueInput,
    UpdateIssueOutput,
)
from overbae.services.mcp.errors import MCPError


def _capability_id(context: MCPContext, reference: str | None):
    if not reference:
        return None
    capability, error = resolve_capability(context.project, reference)
    if capability is None:
        raise MCPError("capability_not_found", error or "The capability was not found.")
    return capability.pk


def _issue_contract(row: dict) -> IssueContract:
    return IssueContract(
        id=str(row["id"]),
        title=row["title"],
        cause=row["cause"],
        severity=row["severity"],
        state=row["state"],
        capability=str(row["capability"]) if row["capability"] else None,
        capability_name=row["capability_name"],
        traces=row["traces"],
        rate=row["rate"],
        prior_rate=row["prior_rate"],
        first_seen=row["first_seen"],
        last_seen=row["last_seen"],
        remedy=row["remedy"],
        trend=row["trend"],
    )


def _page(limit: int, offset: int, total: int) -> PageContract:
    return PageContract(limit=limit, offset=offset, total=total, has_more=offset + limit < total)


def _training_actions(project_id, capability, name: str, range_key: str):
    """The path from a signal to a model. Creating datasets is free; the agent still checks
    readiness and cost, and a person approves the run."""
    plan = training_plan(project_id, capability, range_key)
    return [
        NextActionContract(
            tool="create_dataset_from_traces",
            arguments={
                "name": f"{name} passing traces",
                "capability": str(capability),
                "filters": _without_project(plan["selection"]["filters"]),
                "limit": plan["selection"]["limit"],
                "split": {"eval_percent": plan["eval_percent"], "position": "random"},
            },
            why="Passing traces train and test the model. Failures stay out: they record the "
            "wrong answer. To train on them, correct them in a retained dataset pipeline first.",
        ),
        NextActionContract(
            tool="check_finetune_readiness",
            why="Confirm the prepared data can train before estimating the run.",
        ),
    ]


def _without_project(filters: dict) -> dict:
    return {key: value for key, value in filters.items() if key != "project"}


def _signals_sync(
    payload: ListTrainingSignalsInput, context: MCPContext
) -> ListTrainingSignalsOutput:
    project_id = context.project.pk
    capability = _capability_id(context, payload.capability)
    rows = training_signal_rows(project_id, payload.range, capability)
    window = rows[payload.offset : payload.offset + payload.limit]
    checked = overview(project_id, payload.range, capability)["traces_checked"]
    return ListTrainingSignalsOutput(
        summary=f"{len(rows)} capabilities to train in {payload.range}.",
        traces_checked=checked,
        signals=[
            TrainingSignalContract(
                capability=str(item["capability"]),
                capability_name=item["capability_name"],
                model=item["model"],
                issues=[_issue_contract(row) for row in item["issues"]],
                more_issues=item["more_issues"],
                failing_traces=item["failing_traces"],
                passing_traces=item["passing_traces"],
                next_actions=_training_actions(
                    project_id, item["capability"], item["capability_name"], payload.range
                ),
            )
            for item in window
        ],
        page=_page(payload.limit, payload.offset, len(rows)),
    )


def _issues_sync(payload: ListIssuesInput, context: MCPContext) -> ListIssuesOutput:
    capability = _capability_id(context, payload.capability)
    page_number = payload.offset // payload.limit + 1
    data = issue_page(
        context.project.pk, payload.range, capability, payload.cause, page_number, payload.limit
    )
    return ListIssuesOutput(
        summary=f"{data['count']} repeating issues in {payload.range}.",
        issues=[_issue_contract(row) for row in data["results"]],
        page=_page(payload.limit, payload.offset, data["count"]),
    )


def _project_issue(context: MCPContext, reference: str) -> Issue:
    try:
        issue_id = uuid.UUID(reference)
    except ValueError:
        raise MCPError("invalid_input", "issue must be an issue id.") from None
    issue = (
        Issue.objects.filter(pk=issue_id, project=context.project)
        .select_related("capability")
        .first()
    )
    if issue is None:
        raise MCPError("issue_not_found", "The issue was not found in this project.")
    return issue


def _inspect_sync(payload: InspectIssueInput, context: MCPContext) -> InspectIssueOutput:
    issue = _project_issue(context, payload.issue)
    detail = issue_detail(issue, payload.range)
    next_actions = (
        _training_actions(
            context.project.pk, issue.capability_id, detail["capability_name"], payload.range
        )
        if issue.cause == Issue.Cause.MODEL and issue.capability_id
        else []
    )
    next_actions.append(
        NextActionContract(
            tool="query_traces",
            arguments={"issue": str(issue.pk)},
            why="Read every trace showing this issue.",
        )
    )
    return InspectIssueOutput(
        summary=f"{detail['title'][:300]}: {detail['traces']} traces in {payload.range}.",
        issue=_issue_contract(detail),
        sessions=detail["sessions"],
        causes=detail["causes"],
        examples=detail["examples"],
        related=[{**item, "id": str(item["id"])} for item in detail["related"]],
        next_actions=next_actions,
    )


def _update_sync(payload: UpdateIssueInput, context: MCPContext) -> UpdateIssueOutput:
    if payload.state is None and payload.title is None:
        raise MCPError("invalid_input", "Pass state, title or both.")
    issue = _project_issue(context, payload.issue)
    change_issue(issue, state=payload.state, title=payload.title)
    detail = issue_detail(issue, "7d")
    return UpdateIssueOutput(
        summary=f"{detail['title'][:300]} is {detail['state']}.",
        issue=_issue_contract(detail),
    )


def _async_handler(function):
    async def handler(payload, context):
        return await sync_to_async(function, thread_sensitive=True)(payload, context)

    return handler


def register_issue_tools(catalog) -> None:
    from overbae.services.mcp.catalog import ToolDefinition

    for name, title, description, input_model, output_model, function in (
        (
            "list_training_signals",
            "List signals to train",
            "Start here to decide what to train. Ranks every capability whose repeating failures "
            "a model can learn from, with exact failing and passing trace counts and the next "
            "tools to call. Counts are exact across all traffic.",
            ListTrainingSignalsInput,
            ListTrainingSignalsOutput,
            _signals_sync,
        ),
        (
            "list_issues",
            "List issues",
            "Page failures that repeat across traces and time, ranked by severity, state and "
            "rate, with exact rates against all traffic. cause=model can be trained away; system "
            "and data need a fix outside the model.",
            ListIssuesInput,
            ListIssuesOutput,
            _issues_sync,
        ),
        (
            "inspect_issue",
            "Inspect issue",
            "Read one issue: its causes, redacted examples, the same failure on other "
            "capabilities and the next tools to call.",
            InspectIssueInput,
            InspectIssueOutput,
            _inspect_sync,
        ),
    ):
        catalog.register(
            ToolDefinition(
                name=name,
                title=title,
                description=description,
                input_model=input_model,
                output_model=output_model,
                read_only=True,
                idempotent=True,
                open_world=False,
                required_scopes=frozenset({"overmind:read"}),
                cost_class="free",
                async_mode="sync",
            ),
            _async_handler(function),
        )
    catalog.register(
        ToolDefinition(
            name="update_issue",
            title="Update issue",
            description="Resolve, mute or reopen an issue, or rename it once the traces explain "
            "the failure. A resolved issue that recurs comes back as regressed.",
            input_model=UpdateIssueInput,
            output_model=UpdateIssueOutput,
            read_only=False,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:observability:write"}),
            cost_class="free",
            async_mode="sync",
        ),
        _async_handler(_update_sync),
    )
