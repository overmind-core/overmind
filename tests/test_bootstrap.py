"""Tests for the first-run bootstrap logic."""

from sqlalchemy import select


async def test_bootstrap_creates_user_project_token(db_session):
    """ensure_default_user creates admin + project + token on empty DB."""
    from overmind.bootstrap import ensure_default_user
    from overmind.models.iam.users import User
    from overmind.models.iam.projects import Project
    from overmind.models.iam.tokens import Token

    await ensure_default_user(db_session)

    users = (await db_session.execute(select(User))).scalars().all()
    assert len(users) == 1
    assert users[0].email == "admin"

    projects = (await db_session.execute(select(Project))).scalars().all()
    assert len(projects) == 1
    assert projects[0].slug == "default-project"

    tokens = (await db_session.execute(select(Token))).scalars().all()
    assert len(tokens) == 1
    assert tokens[0].is_active is True


async def test_bootstrap_is_idempotent(db_session):
    """Calling ensure_default_user twice should not create duplicates."""
    from overmind.bootstrap import ensure_default_user
    from overmind.models.iam.users import User

    await ensure_default_user(db_session)
    await ensure_default_user(db_session)

    count = len((await db_session.execute(select(User))).scalars().all())
    assert count == 1


async def test_default_user_can_login(seed_user, test_client):
    """The bootstrapped admin user can log in with default credentials."""
    resp = await test_client.post(
        "/api/v1/iam/users/login",
        json={"email": "admin", "password": "admin"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data["user"]["email"] == "admin"
