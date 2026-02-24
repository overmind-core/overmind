"""Tests for the auto-evaluation scanner and execution logic."""

import pytest
from sqlalchemy import select

from overmind.models.jobs import Job


@pytest.mark.asyncio
async def test_scanner_creates_job_for_eligible_prompt(
    seed_user, db_session, prompt_factory, span_factory
):
    """A prompt with criteria + >=10 unscored spans should get a pending job."""
    from overmind.tasks.evaluations import validate_judge_scoring_eligibility

    user, project, _ = seed_user

    prompt = await prompt_factory(
        project_id=project.project_id,
        user_id=user.user_id,
        slug="eval-me",
        evaluation_criteria={"correctness": ["Must be accurate", "Must be helpful"]},
    )

    prompt_id = prompt.prompt_id
    for _ in range(12):
        await span_factory(
            project_id=project.project_id,
            user_id=user.user_id,
            prompt_id=prompt_id,
            feedback_score={},
        )
    await db_session.commit()

    is_eligible, error, stats = await validate_judge_scoring_eligibility(
        prompt, db_session
    )
    assert is_eligible is True, f"Expected eligible but got: {error}"
    assert stats["unscored_spans_count"] >= 10


@pytest.mark.asyncio
async def test_scanner_skips_ineligible_prompt(
    seed_user, db_session, prompt_factory, span_factory
):
    """A prompt with fewer than 10 unscored spans should NOT be eligible."""
    from overmind.tasks.evaluations import validate_judge_scoring_eligibility

    user, project, _ = seed_user

    prompt = await prompt_factory(
        project_id=project.project_id,
        user_id=user.user_id,
        slug="too-few",
        evaluation_criteria={"correctness": ["Must be accurate"]},
    )

    prompt_id = prompt.prompt_id
    for _ in range(5):
        await span_factory(
            project_id=project.project_id,
            user_id=user.user_id,
            prompt_id=prompt_id,
            feedback_score={},
        )
    await db_session.commit()

    is_eligible, error, stats = await validate_judge_scoring_eligibility(
        prompt, db_session
    )
    assert is_eligible is False
    assert stats["unscored_spans_count"] < 10


@pytest.mark.asyncio
async def test_scanner_skips_prompt_without_criteria(
    seed_user, db_session, prompt_factory, span_factory
):
    """A prompt without evaluation_criteria should NOT be eligible."""
    from overmind.tasks.evaluations import validate_judge_scoring_eligibility

    user, project, _ = seed_user

    prompt = await prompt_factory(
        project_id=project.project_id,
        user_id=user.user_id,
        slug="no-criteria",
        evaluation_criteria=None,
    )

    for _ in range(15):
        await span_factory(
            project_id=project.project_id,
            user_id=user.user_id,
            prompt_id=prompt.prompt_id,
        )
    await db_session.commit()

    is_eligible, error, _ = await validate_judge_scoring_eligibility(
        prompt, db_session
    )
    assert is_eligible is False
    assert "criteria" in error.lower()
