"""Tests for the auto-evaluation scanner and execution logic."""

import pytest


@pytest.mark.parametrize(
    "span_count,has_criteria,expected_eligible",
    [
        (12, True, True),
        (5, True, False),
        (15, False, False),
    ],
    ids=["enough-spans-with-criteria", "too-few-spans", "no-criteria"],
)
async def test_judge_scoring_eligibility(
    seed_user,
    db_session,
    prompt_factory,
    span_factory,
    span_count,
    has_criteria,
    expected_eligible,
):
    from overmind.tasks.evaluations import validate_judge_scoring_eligibility

    user = seed_user.user
    project = seed_user.project

    criteria = (
        {"correctness": ["Must be accurate", "Must be helpful"]}
        if has_criteria
        else None
    )

    prompt = await prompt_factory(
        project_id=project.project_id,
        user_id=user.user_id,
        slug=f"eval-{span_count}-{has_criteria}",
        evaluation_criteria=criteria,
    )

    for _ in range(span_count):
        await span_factory(
            project_id=project.project_id,
            user_id=user.user_id,
            prompt_id=prompt.prompt_id,
            feedback_score={},
        )
    await db_session.commit()

    is_eligible, error, stats = await validate_judge_scoring_eligibility(
        prompt, db_session
    )
    assert is_eligible is expected_eligible

    if expected_eligible:
        assert stats["unscored_spans_count"] >= 10
    elif has_criteria:
        assert stats["unscored_spans_count"] < 10
    else:
        assert "criteria" in error.lower()
