"""Tests for the job cleanup task logic."""

import pytest
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from overmind.models.jobs import Job


async def test_cleanup_deletes_old_terminal_jobs(
    seed_user, db_session, job_factory, patch_task_session
):
    """System-triggered completed jobs older than 24h should be deleted."""
    from overmind.tasks.job_cleanup import _cleanup_old_jobs

    project = seed_user.project
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

    p1, p2 = patch_task_session("overmind.tasks.job_cleanup")
    with p1, p2:
        result = await _cleanup_old_jobs()

    assert result["deleted"] == 2

    remaining = (await db_session.execute(select(Job))).scalars().all()
    assert len(remaining) == 1
    assert remaining[0].job_id == recent_job.job_id


@pytest.mark.parametrize(
    "triggered_by_user,expected_deleted",
    [
        (True, 0),
        (False, 1),
    ],
    ids=["user-triggered-preserved", "system-triggered-deleted"],
)
async def test_cleanup_respects_triggered_by(
    seed_user,
    db_session,
    job_factory,
    patch_task_session,
    triggered_by_user,
    expected_deleted,
):
    """User-triggered jobs should be preserved, system-triggered deleted."""
    from overmind.tasks.job_cleanup import _cleanup_old_jobs

    user = seed_user.user
    project = seed_user.project
    old_time = datetime.now(timezone.utc) - timedelta(hours=48)

    await job_factory(
        project_id=project.project_id,
        job_type="judge_scoring",
        status="completed",
        triggered_by_user_id=user.user_id if triggered_by_user else None,
        created_at=old_time,
    )
    await db_session.commit()

    p1, p2 = patch_task_session("overmind.tasks.job_cleanup")
    with p1, p2:
        result = await _cleanup_old_jobs()

    assert result["deleted"] == expected_deleted
