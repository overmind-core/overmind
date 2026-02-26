"""Tests for the agent discovery eligibility logic."""

import pytest


@pytest.mark.parametrize(
    "span_count,expected_eligible",
    [
        (12, True),
        (5, False),
    ],
    ids=["enough-spans", "too-few-spans"],
)
async def test_agent_discovery_eligibility(
    seed_user, db_session, span_factory, span_count, expected_eligible
):
    from overmind.tasks.agent_discovery import validate_agent_discovery_eligibility

    user = seed_user.user
    project = seed_user.project

    for _ in range(span_count):
        await span_factory(
            project_id=project.project_id,
            user_id=user.user_id,
            prompt_id=None,
            input_data={"messages": [{"role": "user", "content": "What is AI?"}]},
        )
    await db_session.commit()

    is_eligible, error, stats = await validate_agent_discovery_eligibility(
        project.project_id, db_session
    )
    assert is_eligible is expected_eligible
