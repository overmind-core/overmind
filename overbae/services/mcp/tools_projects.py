from uuid import UUID

from asgiref.sync import sync_to_async
from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field

from overbae.models import APIToken
from overbae.services.mcp.context import accessible_projects
from overbae.services.mcp.contracts.projects import CreateApiKeyInput, CreateApiKeyOutput
from overbae.services.mcp.errors import MCPError
from overbae.services.mcp.resources import interface_resource


class ListProjectsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=50, ge=1, le=100)


class ProjectSummary(BaseModel):
    id: str
    name: str
    slug: str
    console_url: str
    resource_uri: str


class ListProjectsOutput(BaseModel):
    projects: list[ProjectSummary]
    total: int
    next_offset: int | None
    connection: dict
    catalog_sha256: str
    tool_count: int


@sync_to_async(thread_sensitive=True)
def list_projects(payload, context):
    identity = interface_resource()
    projects = accessible_projects(context)
    total = projects.count()
    end = payload.offset + payload.limit
    return ListProjectsOutput(
        connection=identity["connection"],
        catalog_sha256=identity["catalog_sha256"],
        tool_count=identity["tool_count"],
        projects=[
            ProjectSummary(
                id=str(project.pk),
                name=project.name,
                slug=project.slug,
                console_url=f"{settings.FRONTEND_URL.rstrip('/')}/?projectId={project.pk}",
                resource_uri=f"overmind://project/current?project_id={project.pk}",
            )
            for project in projects[payload.offset : end]
        ],
        total=total,
        next_offset=end if end < total else None,
    )


def _key_project(payload: CreateApiKeyInput, context):
    scope = context.token.scope if isinstance(context.token.scope, dict) else {}
    account = scope.get("scope") == "account"
    if payload.project_id is None:
        if account:
            return None
        raise MCPError(
            "permission_denied",
            "A project API key can only create a key for its own project.",
        )
    if not account and str(payload.project_id) != str(context.project.id):
        raise MCPError(
            "permission_denied",
            "A project API key can only create a key for its own project.",
        )
    try:
        selected = UUID(str(payload.project_id))
    except ValueError:
        raise MCPError("invalid_input", "project_id must be a UUID.") from None
    project = accessible_projects(context).filter(pk=selected).first()
    if project is None:
        raise MCPError("project_required", "The project is not accessible to this connection.")
    return project


def _create_api_key(payload: CreateApiKeyInput, context) -> CreateApiKeyOutput:
    if context.user.is_guest:
        raise MCPError("permission_denied", "Create an account to continue.")
    raw, instance = APIToken.create_for_user(
        context.user,
        name=payload.name,
        project=_key_project(payload, context),
        description=payload.description,
        expires_in_days=payload.expires_in_days,
    )
    return CreateApiKeyOutput(
        id=str(instance.id),
        key=raw,
        name=instance.name,
        description=instance.description,
        prefix=instance.prefix,
        scope=instance.scope["scope"],
        project_id=str(instance.project_id) if instance.project_id else None,
        expires_at=instance.expires_at,
        created_at=instance.created_at,
    )


def register_project_tools(catalog):
    from overbae.services.mcp.catalog import ToolDefinition

    catalog.register(
        ToolDefinition(
            name="create_api_key",
            title="Create API key",
            description=(
                "Create an API key. name, description and expires_in_days (0, 30, 90, 180, 365; "
                "0 does not expire) match the Console dialog. Omit project_id for an account key; "
                "pass project_id for a project key. The plaintext is returned once. A project "
                "connection can only create a key for its own project."
            ),
            input_model=CreateApiKeyInput,
            output_model=CreateApiKeyOutput,
            read_only=False,
            idempotent=False,
            open_world=False,
            required_scopes=frozenset({"overmind:account:write"}),
            cost_class="free",
            async_mode="sync",
            project_scoped=False,
        ),
        sync_to_async(_create_api_key, thread_sensitive=True),
    )
    catalog.register(
        ToolDefinition(
            name="list_projects",
            title="List accessible projects",
            description="List active projects this connection may access. Pass the chosen id as project_id on subsequent tools and resources; selection is per request.",
            input_model=ListProjectsInput,
            output_model=ListProjectsOutput,
            read_only=True,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:read"}),
            cost_class="free",
            async_mode="sync",
            project_scoped=False,
        ),
        list_projects,
    )
