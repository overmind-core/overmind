"""Shared fixtures for task tests."""

from unittest.mock import AsyncMock, patch

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


@pytest_asyncio.fixture()
async def task_session_factory(test_engine):
    """Session factory bound to the test engine, for patching into task modules."""
    return async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest_asyncio.fixture()
async def patch_task_session(task_session_factory):
    """Returns a context-manager factory that patches get_session_local and dispose_engine
    for a given task module path.

    dispose_engine_path defaults to '{module_path}.dispose_engine' but can be overridden
    for modules that import it locally (e.g. 'overmind.db.session.dispose_engine').
    """

    def _patch(module_path: str, dispose_engine_path: str | None = None):
        if dispose_engine_path is None:
            dispose_engine_path = f"{module_path}.dispose_engine"
        return (
            patch(
                f"{module_path}.get_session_local", return_value=task_session_factory
            ),
            patch(dispose_engine_path, new_callable=AsyncMock),
        )

    return _patch
