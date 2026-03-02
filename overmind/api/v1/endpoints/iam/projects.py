"""
Core IAM – project CRUD.

Simplified project management with no Organisation scoping or RBAC.
Auth is a simple ownership check via the user_projects association.
"""

import re
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from overmind.api.v1.helpers.authentication import (
    AuthenticatedUserOrToken,
    get_current_user,
)
from overmind.api.v1.helpers.responses import (
    conflict_response,
    forbidden_response,
    not_found_response,
    success_response,
)
from overmind.db.session import get_db
from overmind.db.valkey import delete_key
from overmind.models.iam.projects import Project
from overmind.models.iam.relationships import user_project_association
from overmind.models.pydantic_models.core_models import CoreProjectModel

router = APIRouter(prefix="/projects", tags=["Projects"])


def _slugify(name: str) -> str:
    slug = name.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    return slug.strip("-")


# ── request / response schemas ────────────────────────────────────────────


class CreateProjectRequest(BaseModel):
    name: str
    description: str | None = None


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None


class ProjectListResponse(BaseModel):
    projects: list[CoreProjectModel]
    total_count: int


# ── helpers ───────────────────────────────────────────────────────────────


async def _verify_project_access(
    project_id: UUID,
    current_user: AuthenticatedUserOrToken,
    db: AsyncSession,
) -> Project:
    """Load a project and verify the user has access to it."""
    result = await db.execute(select(Project).where(Project.project_id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise not_found_response("Project not found")

    if not await current_user.is_project_member(project_id, db):
        raise forbidden_response("Access denied to this project")

    return project


# ── endpoints ─────────────────────────────────────────────────────────────


@router.post("/", response_model=CoreProjectModel)
async def create_project(
    request: CreateProjectRequest,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a new project and associate it with the current user."""
    slug = _slugify(request.name)

    existing = await db.execute(select(Project.project_id).where(Project.slug == slug))
    if existing.first():
        raise conflict_response("A project with this name already exists")

    project = Project(
        name=request.name,
        slug=slug,
        description=request.description or "",
        is_active=True,
    )
    db.add(project)
    await db.flush()

    await db.execute(
        user_project_association.insert().values(
            user_id=current_user.user_id,
            project_id=project.project_id,
        )
    )
    await db.commit()
    user_id = current_user.user_id
    cache_key = f"user:{user_id}"
    await delete_key(cache_key)

    return CoreProjectModel.model_validate(project)


@router.get("/", response_model=ProjectListResponse)
async def list_projects(
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all projects the current user belongs to."""
    result = await db.execute(
        select(Project)
        .join(
            user_project_association,
            Project.project_id == user_project_association.c.project_id,
        )
        .where(user_project_association.c.user_id == current_user.user_id)
        .order_by(Project.created_at.desc())
    )
    projects = result.scalars().all()

    return ProjectListResponse(
        projects=[CoreProjectModel.model_validate(p) for p in projects],
        total_count=len(projects),
    )


@router.get("/{project_id}", response_model=CoreProjectModel)
async def get_project(
    project_id: UUID,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single project by ID."""
    project = await _verify_project_access(project_id, current_user, db)
    return CoreProjectModel.model_validate(project)


@router.put("/{project_id}", response_model=CoreProjectModel)
async def update_project(
    project_id: UUID,
    request: UpdateProjectRequest,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a project's name or description."""
    project = await _verify_project_access(project_id, current_user, db)

    if request.name is not None:
        new_slug = _slugify(request.name)
        if new_slug != project.slug:
            clash = await db.execute(
                select(Project.project_id).where(
                    Project.slug == new_slug,
                    Project.project_id != project_id,
                )
            )
            if clash.first():
                raise conflict_response("A project with this name already exists")
        project.name = request.name
        project.slug = new_slug
    if request.description is not None:
        project.description = request.description

    await db.commit()
    await db.refresh(project)

    return CoreProjectModel.model_validate(project)


@router.delete("/{project_id}")
async def delete_project(
    project_id: UUID,
    current_user: AuthenticatedUserOrToken = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a project and all associated tokens."""
    project = await _verify_project_access(project_id, current_user, db)

    from overmind.models.iam.tokens import Token
    from overmind.db.valkey import delete_key

    tokens = await db.execute(select(Token).where(Token.project_id == project_id))
    for token in tokens.scalars().all():
        await delete_key(f"token:{token.token_hash}")
        await db.delete(token)

    await db.execute(
        user_project_association.delete().where(
            user_project_association.c.project_id == project_id
        )
    )

    await db.delete(project)
    await db.commit()

    return success_response(message="Project deleted successfully")
