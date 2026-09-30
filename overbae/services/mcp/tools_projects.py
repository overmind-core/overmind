from asgiref.sync import sync_to_async
from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field

from overbae.services.mcp.context import accessible_projects


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


@sync_to_async(thread_sensitive=True)
def list_projects(payload, context):
    projects = accessible_projects(context)
    total = projects.count()
    end = payload.offset + payload.limit
    return ListProjectsOutput(
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


def register_project_tools(catalog):
    from overbae.services.mcp.catalog import ToolDefinition

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
