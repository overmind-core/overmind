"""Tests for the job cleanup task logic."""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from overmind.models.jobs import Job


@pytest.mark.asyncio
async def test_cleanup_deletes_old_terminal_jobs(
    seed_user, db_session, job_factory, test_engine
):
    """System-triggered completed jobs older than 24h should be deleted."""
    from overmind.tasks.job_cleanup import _cleanup_old_jobs

    _, project, _ = seed_user
    old_time = datetime.now(timezone.utc) - timedelta(hours=48)

    await job_factory(
        project_id=project.project_id,
        job_type="judge_scoring",
        status="completed",
        created_at=old_time,
    )
    await job_factory(
        project_id=project.project_id,
        job_type="judge_scoring",
        status="failed",
        created_at=old_time,
    )
    recent_job = await job_factory(
        project_id=project.project_id,
        job_type="judge_scoring",
        status="completed",
    )
    await db_session.commit()

    test_session_factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )

    with (
        patch(
            "overmind.tasks.job_cleanup.get_session_local",
            return_value=test_session_factory,
        ),
        patch("overmind.tasks.job_cleanup.dispose_engine", new_callable=AsyncMock),
    ):
        result = await _cleanup_old_jobs()

    assert result["deleted"] == 2

    remaining = (await db_session.execute(select(Job))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].job_id == recent_job.job_id


@pytest.mark.asyncio
async def test_cleanup_preserves_user_triggered_jobs(
    seed_user, db_session, job_factory, test_engine
):
    """Jobs with triggered_by_user_id set should NOT be deleted."""
    from overmind.tasks.job_cleanup import _cleanup_old_jobs

    user, project, _ = seed_user
    old_time = datetime.now(timezone.utc) - timedelta(hours=48)

    await job_factory(
        project_id=project.project_id,
        job_type="judge_scoring",
        status="completed",
        triggered_by_user_id=user.user_id,
        created_at=old_time,
    )
    await db_session.commit()

    test_session_factory = async_sessionmaker(
        bind=test_engine, class_=AsyncSession, expire_on_commit=False
    )

    with (
        patch(
            "overmind.tasks.job_cleanup.get_session_local",
            return_value=test_session_factory,
        ),
        patch("overmind.tasks.job_cleanup.dispose_engine", new_callable=AsyncMock),
    ):
        result = await _cleanup_old_jobs()

    assert result["deleted"] == 0

    remaining = (await db_session.execute(select(Job))).scalars().all()
    assert len(remaining) == 1
