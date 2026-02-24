"""
Core bootstrap – auto-provision a default admin user, project, and API token
on first startup when the database is empty.

This module is used by overmind's standalone main.py.
Enterprise (overmind_backend) does NOT call this; it has its own signup /
invitation flow.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from overmind.api.v1.helpers.authentication import generate_token, hash_password
from overmind.models.iam.projects import Project
from overmind.models.iam.relationships import user_project_association
from overmind.models.iam.tokens import Token
from overmind.models.iam.users import User

logger = logging.getLogger(__name__)

DEFAULT_ADMIN_EMAIL = "admin"
DEFAULT_ADMIN_PASSWORD = "admin"


async def ensure_default_user(db: AsyncSession) -> None:
    """Create default admin user, project, and token on first run.

    If *any* user already exists the function returns immediately — this
    ensures the bootstrap runs only once (on a completely fresh database).
    """
    result = await db.execute(select(User).limit(1))
    if result.scalar_one_or_none() is not None:
        return  # already provisioned

    # ── user ──────────────────────────────────────────────────────────
    user = User(
        email=DEFAULT_ADMIN_EMAIL,
        full_name="Admin",
        hashed_password=hash_password(DEFAULT_ADMIN_PASSWORD),
        is_active=True,
        is_verified=True,
    )
    db.add(user)
    await db.flush()

    # ── project ───────────────────────────────────────────────────────
    project = Project(
        name="Default Project",
        slug="default-project",
        description="Auto-created default project",
        is_active=True,
    )
    db.add(project)
    await db.flush()

    # associate user ↔ project
    await db.execute(
        user_project_association.insert().values(
            user_id=user.user_id,
            project_id=project.project_id,
        )
    )

    # ── API token ─────────────────────────────────────────────────────
    full_token, token_hash, prefix = generate_token()

    token = Token(
        name="Default Token",
        token_hash=token_hash,
        prefix=prefix,
        user_id=user.user_id,
        project_id=project.project_id,
        is_active=True,
    )
    db.add(token)
    await db.commit()

    logger.info(
        "=== FIRST RUN: provisioned default user ===\n"
        "  username:     %s\n"
        "  password:     %s\n"
        "  project:      %s (id: %s)\n"
        "  API token:    %s\n"
        "Change the default password after first login.",
        DEFAULT_ADMIN_EMAIL,
        DEFAULT_ADMIN_PASSWORD,
        project.name,
        project.project_id,
        full_token,
    )
