from asgiref.sync import sync_to_async
from django.conf import settings

from overbae.models import MCPOAuthToken
from overbae.services import activity
from overbae.services import feedback as feedback_service
from overbae.services.mcp import CONTRACT_VERSION
from overbae.services.mcp.context import project_context
from overbae.services.mcp.contracts.feedback import SubmitFeedbackInput, SubmitFeedbackOutput
from overbae.services.mcp.errors import MCPError

FEEDBACK_TOOL = "submit_feedback"


def _credential(context) -> str:
    if isinstance(context.token, MCPOAuthToken):
        return "oauth"
    return f"{context.token.scope.get('scope', 'unknown')} key"


@sync_to_async(thread_sensitive=True)
def submit_feedback(payload, context):
    project = context.project
    if payload.project_id is not None:
        project = project_context(context, str(payload.project_id)).project
    _, client = activity.connection_actor(context)
    observed = {
        "client": client,
        "client_name": context.client_name,
        "user_agent": context.user_agent,
        "protocol_version": context.protocol_version,
        "credential": _credential(context),
        "contract_version": CONTRACT_VERSION,
    }
    try:
        feedback, created = feedback_service.record_agent_report(
            user=context.user,
            project=project,
            report_id=payload.report_id,
            report=payload.model_dump(
                mode="json", exclude={"report_id", "project_id", "environment"}
            ),
            reported=payload.environment.model_dump(mode="json", exclude_none=True),
            observed={key: value for key, value in observed.items() if value},
        )
    except feedback_service.FeedbackError as refused:
        raise MCPError(
            refused.code, str(refused), retryable=refused.code == "rate_limited"
        ) from None
    return SubmitFeedbackOutput(
        report_id=feedback.pk,
        created=created,
        stored={
            "headline": feedback.title,
            "summary": feedback.message,
            "kind": feedback.kind,
            "area": feedback.area,
            "severity": feedback.severity,
            "outcome": feedback.outcome,
            **feedback.detail,
            "environment": feedback.environment.get("reported", {}),
        },
        tell_user=(
            f"Sent a friction report to Overmind: “{feedback.title}”. "
            f"Agent feedback can be turned off in Settings: {settings.FRONTEND_URL.rstrip('/')}/settings"
        ),
    )


def register_feedback_tools(catalog):
    from overbae.services.mcp.catalog import ToolDefinition

    catalog.register(
        ToolDefinition(
            name=FEEDBACK_TOOL,
            title="Report friction with Overmind",
            description=(
                "Report friction with Overmind itself: a failed or contradictory operation, "
                "unclear guidance, a missing capability, stuck work, or repeated attempts without "
                "progress. Call it without asking, once per problem, when the problem ends your "
                "attempt or after three failed attempts; reuse report_id to update the report. "
                "Describe platform behaviour only: never data values, prompts, code, file "
                "contents, paths or credentials. trail lists, in order, the tools, overmind:// "
                "resources and overmind CLI commands you used. Fill environment with what you "
                "know about yourself and leave the rest out. Then show the user tell_user."
            ),
            input_model=SubmitFeedbackInput,
            output_model=SubmitFeedbackOutput,
            read_only=False,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:read"}),
            cost_class="free",
            async_mode="sync",
            project_scoped=False,
        ),
        submit_feedback,
    )
