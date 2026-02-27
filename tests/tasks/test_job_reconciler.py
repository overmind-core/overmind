"""Tests for the job reconciler task logic."""

from unittest.mock import MagicMock, patch


async def test_reconciler_dispatches_pending_job(
    seed_user, db_session, job_factory, patch_task_session
):
    """A pending judge_scoring job with parameters should be dispatched."""
    from overmind.tasks.job_reconciler import _execute_pending_jobs

    project = seed_user.project

    job = await job_factory(
        project_id=project.project_id,
        job_type="judge_scoring",
        status="pending",
        prompt_slug="test-prompt",
        result={
            "parameters": {
                "prompt_id": f"{project.project_id}_1_test-prompt",
                "project_id": str(project.project_id),
                "prompt_slug": "test-prompt",
            }
        },
    )
    await db_session.commit()

    fake_task = MagicMock()
    fake_task.id = "celery-task-123"

    p1, p2 = patch_task_session(
        "overmind.tasks.job_reconciler",
        dispose_engine_path="overmind.db.session.dispose_engine",
    )
    with (
        p1,
        p2,
        patch("overmind.tasks.job_reconciler.celery_app") as mock_celery,
    ):
        mock_celery.send_task.return_value = fake_task
        mock_celery.AsyncResult.return_value = MagicMock(state="PENDING")

        result = await _execute_pending_jobs()

    assert result["jobs_executed"] >= 1

    await db_session.refresh(job)
    assert job.status == "running"
    assert job.celery_task_id == "celery-task-123"


async def test_reconciler_skips_when_duplicate_running(
    seed_user, db_session, job_factory, patch_task_session
):
    """If a job of the same type/prompt is already running, the pending one is skipped."""
    from overmind.tasks.job_reconciler import _execute_pending_jobs

    project = seed_user.project

    await job_factory(
        project_id=project.project_id,
        job_type="judge_scoring",
        status="running",
        prompt_slug="same-prompt",
        celery_task_id="running-task-1",
    )
    pending_job = await job_factory(
        project_id=project.project_id,
        job_type="judge_scoring",
        status="pending",
        prompt_slug="same-prompt",
        result={"parameters": {"prompt_id": "x_1_same-prompt"}},
    )
    await db_session.commit()

    p1, p2 = patch_task_session(
        "overmind.tasks.job_reconciler",
        dispose_engine_path="overmind.db.session.dispose_engine",
    )
    with (
        p1,
        p2,
        patch("overmind.tasks.job_reconciler.celery_app") as mock_celery,
    ):
        mock_celery.AsyncResult.return_value = MagicMock(state="STARTED")
        result = await _execute_pending_jobs()

    assert result["jobs_executed"] == 0
    await db_session.refresh(pending_job)
    assert pending_job.status == "pending"


async def test_reconciler_cleans_stale_running_job(seed_user, db_session, job_factory):
    """A running job whose Celery task has FAILURE state should be marked failed."""
    from overmind.tasks.job_reconciler import _cleanup_stale_running_jobs

    project = seed_user.project

    stale_job = await job_factory(
        project_id=project.project_id,
        job_type="judge_scoring",
        status="running",
        celery_task_id="dead-task-99",
    )
    await db_session.commit()

    mock_result = MagicMock()
    mock_result.state = "FAILURE"
    mock_result.result = Exception("Worker crashed")

    with patch("overmind.tasks.job_reconciler.celery_app") as mock_celery:
        mock_celery.AsyncResult.return_value = mock_result
        cleaned = await _cleanup_stale_running_jobs(db_session)

    assert cleaned == 1
    await db_session.refresh(stale_job)
    assert stale_job.status == "failed"
