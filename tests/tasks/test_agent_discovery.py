"""Tests for the agent discovery eligibility logic."""

import pytest
from uuid import uuid4


@pytest.mark.asyncio
async def test_discovery_eligible_with_enough_spans(
    seed_user, db_session, span_factory
):
    """A project with >=10 unmapped spans should be eligible for discovery."""
    from overmind.tasks.agent_discovery import validate_agent_discovery_eligibility

    user, project, _ = seed_user

    for _ in range(12):
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
    assert is_eligible is True, f"Expected eligible but got: {error}"


@pytest.mark.asyncio
async def test_discovery_ineligible_with_few_spans(
    seed_user, db_session, span_factory
):
    """A project with fewer than 10 spans should NOT be eligible."""
    from overmind.tasks.agent_discovery import validate_agent_discovery_eligibility

    user, project, _ = seed_user

    for _ in range(5):
        await span_factory(
            project_id=project.project_id,
            user_id=user.user_id,
            prompt_id=None,
        )
    await db_session.commit()

    is_eligible, error, _ = await validate_agent_discovery_eligibility(
        project.project_id, db_session
    )
    assert is_eligible is False
