from asgiref.sync import sync_to_async

from overbae.services.mcp import inventory
from overbae.services.mcp.context import accessible_projects
from overbae.services.mcp.contracts.inventory import (
    DependenciesInput,
    DependenciesOutput,
    LineageInput,
    LineageOutput,
    ListObjectsInput,
    ListObjectsOutput,
    OverviewInput,
    OverviewOutput,
    SearchObjectsInput,
    SearchObjectsOutput,
)


async def list_objects(payload, context):
    return await sync_to_async(inventory.list_objects, thread_sensitive=True)(
        context.project, payload
    )


async def inspect_model_lineage(payload, context):
    return await sync_to_async(inventory.lineage, thread_sensitive=True)(context.project, payload)


async def inspect_dependencies(payload, context):
    return await sync_to_async(inventory.dependencies, thread_sensitive=True)(
        context.project, payload
    )


async def project_overview(payload, context):
    return await sync_to_async(inventory.overview, thread_sensitive=True)(context.project, payload)


@sync_to_async(thread_sensitive=True)
def search_objects(payload, context):
    projects = list(accessible_projects(context).filter(pk__in=payload.project_ids))
    found = {project.pk for project in projects}
    missing = [str(pk) for pk in dict.fromkeys(payload.project_ids) if pk not in found]
    result = inventory.list_objects(projects, payload)
    return SearchObjectsOutput(
        **result.model_dump(), inaccessible_project_ids=missing, scope_complete=not missing
    )


def register_inventory_tools(catalog):
    from overbae.services.mcp.catalog import ToolDefinition

    catalog.register(
        ToolDefinition(
            name="search_objects",
            title="Search accessible projects",
            description="Search up to eight explicit accessible projects with globally ordered pagination. Inaccessible project IDs are reported as partial coverage; permissions are never widened.",
            input_model=SearchObjectsInput,
            output_model=SearchObjectsOutput,
            read_only=True,
            idempotent=True,
            open_world=False,
            required_scopes=frozenset({"overmind:read"}),
            cost_class="free",
            async_mode="sync",
            project_scoped=False,
        ),
        search_objects,
    )
    for name, description, input_model, output_model, handler in (
        (
            "inspect_dependencies",
            "Page recorded direct consumers, evaluation memberships and routing for a dataset, deployment, eval set or row. Read-only impact inspection; external/indirect references remain unknown.",
            DependenciesInput,
            DependenciesOutput,
            inspect_dependencies,
        ),
        (
            "inspect_model_lineage",
            "Read pinned training cells, preparation, evaluation references, deployment and active routing. Page evaluations; missing lineage remains unknown. Full recipe/checkpoint evidence is linked.",
            LineageInput,
            LineageOutput,
            inspect_model_lineage,
        ),
        (
            "list_objects",
            "Discover/search project models, jobs, deployments, capabilities, datasets and evaluations. trained_model includes successful chat/decision jobs without deployments. Filter request_key to recover work. Page until next_offset is null; names may be ambiguous.",
            ListObjectsInput,
            ListObjectsOutput,
            list_objects,
        ),
        (
            "get_project_overview",
            "Read project inventory counts by status and recent objects. No provider work; use list_objects for complete filtered pages.",
            OverviewInput,
            OverviewOutput,
            project_overview,
        ),
    ):
        catalog.register(
            ToolDefinition(
                name=name,
                title=name.replace("_", " ").capitalize(),
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
            handler,
        )
