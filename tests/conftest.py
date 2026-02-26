"""
Shared test fixtures for overmind.

Uses real Postgres (from docker-compose) with per-test table
create/drop, mocked Valkey (in-memory dict), and mocked LLM calls.
"""

import os
import hashlib
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

os.environ.setdefault("DEBUG", "false")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-tests")

from overmind.db.base import Base  # noqa: E402
from overmind.main import app  # noqa: E402

TEST_DB_NAME = "overmind_test"
ADMIN_DB_URL = "postgresql+asyncpg://overmind:overmind@postgres:5432/overmind_core"
TEST_DB_URL = f"postgresql+asyncpg://overmind:overmind@postgres:5432/{TEST_DB_NAME}"


# ---------------------------------------------------------------------------
# Database fixtures
# ---------------------------------------------------------------------------

_db_created = False


@pytest_asyncio.fixture(scope="function")
async def test_engine():
    """Create the test database if needed, then yield an async engine."""
    global _db_created
    if not _db_created:
        import asyncpg

        conn = await asyncpg.connect(
            "postgresql://overmind:overmind@postgres:5432/overmind_core"
        )
        dbs = await conn.fetch(
            "SELECT datname FROM pg_database WHERE datname = $1", TEST_DB_NAME
        )
        if not dbs:
            await conn.execute(f'CREATE DATABASE "{TEST_DB_NAME}" TEMPLATE template0')
        await conn.close()
        _db_created = True
    engine = create_async_engine(TEST_DB_URL, pool_pre_ping=True, echo=False)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def db_session(test_engine):
    """Fresh tables per test: drop → create → yield session → drop."""
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    factory = async_sessionmaker(
        bind=test_engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    async with factory() as session:
        yield session

    async with test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


# ---------------------------------------------------------------------------
# Valkey mock (autouse — no live Valkey needed)
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(autouse=True)
async def mock_valkey(monkeypatch):
    """Replace Valkey helpers with an in-memory dict."""
    store: dict[str, str] = {}

    async def _get_key(key: str) -> str | None:
        return store.get(key)

    async def _set_key(key: str, value: str, ttl: int | None = None) -> bool:
        store[key] = value
        return True

    async def _delete_key(key: str) -> bool:
        return store.pop(key, None) is not None

    async def _delete_keys(keys) -> int:
        count = 0
        for k in keys:
            if store.pop(k, None) is not None:
                count += 1
        return count

    async def _delete_keys_by_pattern(pattern: str) -> int:
        return 0

    monkeypatch.setattr("overmind.db.valkey.get_key", _get_key)
    monkeypatch.setattr("overmind.db.valkey.set_key", _set_key)
    monkeypatch.setattr("overmind.db.valkey.delete_key", _delete_key)
    monkeypatch.setattr("overmind.db.valkey.delete_keys", _delete_keys)
    monkeypatch.setattr(
        "overmind.db.valkey.delete_keys_by_pattern", _delete_keys_by_pattern
    )

    # Also patch where these are imported directly
    for mod in [
        "overmind.api.v1.helpers.authentication",
        "overmind.api.v1.endpoints.iam.users",
        "overmind.api.v1.endpoints.iam.projects",
        "overmind.api.v1.endpoints.iam.tokens",
    ]:
        try:
            monkeypatch.setattr(f"{mod}.get_key", _get_key, raising=False)
            monkeypatch.setattr(f"{mod}.set_key", _set_key, raising=False)
            monkeypatch.setattr(f"{mod}.delete_key", _delete_key, raising=False)
        except Exception:
            pass

    yield store
    store.clear()


# ---------------------------------------------------------------------------
# LLM mock
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def mock_llm(monkeypatch):
    """Mock call_llm to return a canned response. Override the callback for custom responses."""
    default_response = ("Mocked LLM response", {
        "prompt_tokens": 10,
        "completion_tokens": 5,
        "response_ms": 100,
        "response_cost": 0.001,
    })

    mock = AsyncMock(return_value=default_response)
    monkeypatch.setattr("overmind.core.llms.call_llm", mock)
    return mock


# ---------------------------------------------------------------------------
# Celery mock
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture()
async def mock_celery(monkeypatch):
    """Capture celery send_task and .delay() calls instead of hitting a broker."""
    dispatched: list[dict[str, Any]] = []

    def fake_send_task(name, args=None, kwargs=None, **kw):
        dispatched.append({"name": name, "args": args, "kwargs": kwargs})
        result = MagicMock()
        result.id = str(uuid4())
        return result

    monkeypatch.setattr(
        "overmind.celery_app.get_celery_app",
        lambda: MagicMock(send_task=fake_send_task),
    )
    return dispatched


# ---------------------------------------------------------------------------
# Test client
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def test_client(db_session):
    from overmind.db.session import get_db
    from overmind.api.v1.helpers.policy_interface import NoopOrgPolicyProvider
    from overmind.api.v1.helpers.authentication import RBACAuthenticationProvider
    from overmind.api.v1.helpers.auth_interface import NoopAuthorizationProvider

    async def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    if not hasattr(app.state, "authentication_provider"):
        app.state.authentication_provider = RBACAuthenticationProvider()
    if not hasattr(app.state, "authorization_provider"):
        app.state.authorization_provider = NoopAuthorizationProvider()
    if not hasattr(app.state, "org_policy_provider"):
        app.state.org_policy_provider = NoopOrgPolicyProvider()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Seed data helpers
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def seed_user(db_session):
    """Bootstrap the default admin + project + token. Returns (user, project, plain_token)."""
    from overmind.models.iam.users import User
    from overmind.models.iam.projects import Project
    from overmind.models.iam.tokens import Token
    from overmind.models.iam.relationships import user_project_association
    from overmind.api.v1.helpers.authentication import hash_password, generate_token

    user = User(
        email="admin",
        full_name="Admin",
        hashed_password=hash_password("admin"),
        is_active=True,
        is_verified=True,
    )
    db_session.add(user)
    await db_session.flush()

    project = Project(
        name="Default Project",
        slug="default-project",
        description="Test project",
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()

    await db_session.execute(
        user_project_association.insert().values(
            user_id=user.user_id, project_id=project.project_id
        )
    )

    full_token, token_hash, prefix = generate_token()
    token = Token(
        name="Default Token",
        token_hash=token_hash,
        prefix=prefix,
        user_id=user.user_id,
        project_id=project.project_id,
        is_active=True,
    )
    db_session.add(token)
    await db_session.commit()

    return user, project, full_token


@pytest_asyncio.fixture(scope="function")
async def auth_headers(seed_user, test_client):
    """JWT auth headers for the seeded admin user."""
    resp = await test_client.post(
        "/api/v1/iam/users/login",
        json={"email": "admin", "password": "admin"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="function")
async def api_token_headers(seed_user):
    """API token auth headers for the seeded token."""
    _, _, full_token = seed_user
    return {"X-API-Token": full_token}


# ---------------------------------------------------------------------------
# Factory fixtures
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture(scope="function")
async def user_factory(db_session):
    from overmind.models.iam.users import User
    from overmind.api.v1.helpers.authentication import hash_password

    async def _create(
        email: str | None = None,
        full_name: str = "Test User",
        password: str = "password123",
    ) -> User:
        user = User(
            email=email or f"user+{uuid4().hex[:6]}@example.com",
            full_name=full_name,
            hashed_password=hash_password(password),
            is_active=True,
            is_verified=True,
        )
        db_session.add(user)
        await db_session.flush()
        return user

    return _create


@pytest_asyncio.fixture(scope="function")
async def project_factory(db_session):
    from overmind.models.iam.projects import Project
    from overmind.models.iam.relationships import user_project_association

    async def _create(user, name: str = "Test Project") -> "Project":
        slug = name.lower().replace(" ", "-")
        project = Project(name=name, slug=slug, description="", is_active=True)
        db_session.add(project)
        await db_session.flush()
        await db_session.execute(
            user_project_association.insert().values(
                user_id=user.user_id, project_id=project.project_id
            )
        )
        await db_session.flush()
        return project

    return _create


@pytest_asyncio.fixture(scope="function")
async def prompt_factory(db_session):
    from overmind.models.prompts import Prompt

    async def _create(
        project_id,
        user_id,
        slug: str = "test-prompt",
        prompt_text: str = "You are a helpful assistant",
        version: int = 1,
        evaluation_criteria: dict | None = None,
        agent_description: dict | None = None,
    ) -> Prompt:
        prompt = Prompt(
            slug=slug,
            hash=hashlib.sha256(prompt_text.encode()).hexdigest(),
            prompt=prompt_text,
            display_name=slug,
            user_id=user_id,
            project_id=project_id,
            version=version,
            evaluation_criteria=evaluation_criteria,
            agent_description=agent_description,
        )
        db_session.add(prompt)
        await db_session.flush()
        return prompt

    return _create


@pytest_asyncio.fixture(scope="function")
async def span_factory(db_session):
    from overmind.models.traces import TraceModel, SpanModel

    async def _create(
        project_id,
        user_id,
        prompt_id: str | None = None,
        operation: str = "llm.chat",
        feedback_score: dict | None = None,
        input_data: dict | None = None,
        output_data: dict | None = None,
        trace_id=None,
    ) -> SpanModel:
        if trace_id is None:
            now_nano = int(time.time() * 1_000_000_000)
            trace = TraceModel(
                application_name="test-app",
                source="test",
                version="1.0",
                start_time_unix_nano=now_nano,
                end_time_unix_nano=now_nano + 1_000_000,
                status_code=0,
                input_params={},
                output_params={},
                input=input_data or {"messages": [{"role": "user", "content": "hi"}]},
                output=output_data or {"content": "hello"},
                metadata_attributes={},
                feedback_score={},
                project_id=project_id,
                user_id=user_id,
            )
            db_session.add(trace)
            await db_session.flush()
            trace_id = trace.trace_id

        now_nano = int(time.time() * 1_000_000_000)
        span = SpanModel(
            span_id=uuid4().hex[:36],
            operation=operation,
            start_time_unix_nano=now_nano,
            end_time_unix_nano=now_nano + 500_000,
            input_params={},
            output_params={},
            input=input_data or {"messages": [{"role": "user", "content": "hi"}]},
            output=output_data or {"content": "hello"},
            status_code=0,
            metadata_attributes={},
            feedback_score=feedback_score or {},
            trace_id=trace_id,
            prompt_id=prompt_id,
        )
        db_session.add(span)
        await db_session.flush()
        return span

    return _create


@pytest_asyncio.fixture(scope="function")
async def job_factory(db_session):
    from overmind.models.jobs import Job

    async def _create(
        project_id,
        job_type: str = "judge_scoring",
        status: str = "pending",
        prompt_slug: str | None = None,
        celery_task_id: str | None = None,
        triggered_by_user_id=None,
        result: dict | None = None,
        created_at: datetime | None = None,
    ) -> Job:
        job = Job(
            job_type=job_type,
            project_id=project_id,
            status=status,
            prompt_slug=prompt_slug,
            celery_task_id=celery_task_id,
            triggered_by_user_id=triggered_by_user_id,
            result=result,
        )
        db_session.add(job)
        await db_session.flush()

        if created_at is not None:
            await db_session.execute(
                text("UPDATE jobs SET created_at = :ts WHERE job_id = :jid"),
                {"ts": created_at, "jid": job.job_id},
            )
            await db_session.flush()

        return job

    return _create
