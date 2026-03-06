"""
Stage 4: LLM judge scoring — trigger evaluations and verify spans get
correctness scores.

Requires agents to be discovered (stage 3) and 10+ unscored spans per prompt.

IMPORTANT: Agent discovery triggers criteria generation asynchronously via
Celery.  The scoring endpoint requires criteria to exist, so we must wait
for criteria before triggering scoring.

Expected outcomes:
- Evaluation criteria are generated for BOTH agents (from async Celery task).
- Scoring jobs complete for BOTH discovered agents.
- Each agent has at least 10 scored spans afterwards.
"""

import pytest

from helpers.api_client import OvermindAPIClient
from helpers.polling import poll_until, wait_for_job_completion

pytestmark = [pytest.mark.e2e, pytest.mark.stage_eval]

EXPECTED_AGENT_COUNT = 2
MIN_SCORED_SPANS_PER_AGENT = 10


def test_evaluation_criteria_generated(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Wait for criteria generation to complete for ALL agents.

    Criteria are generated asynchronously after agent discovery.  Scoring
    requires them, so we gate on this before proceeding.
    """
    prompt_slugs = shared_state.get("prompt_slugs", {})
    assert len(prompt_slugs) == EXPECTED_AGENT_COUNT, (
        f"Expected {EXPECTED_AGENT_COUNT} agents from stage 3, "
        f"got {len(prompt_slugs)}: {list(prompt_slugs.keys())}"
    )

    for slug, prompt_id in prompt_slugs.items():

        def _check(pid=prompt_id):
            resp = overmind_client.get_prompt_criteria(pid)
            criteria = resp.get("evaluation_criteria")
            if criteria and isinstance(criteria, dict) and len(criteria) > 0:
                return criteria
            return None

        criteria = poll_until(
            _check,
            timeout_s=120,
            interval_s=5,
            description=f"criteria for agent '{slug}' (prompt {prompt_id})",
        )

        assert "correctness" in criteria, (
            f"Criteria for '{slug}' missing 'correctness' key. "
            f"Keys: {list(criteria.keys())}"
        )


def _trigger_or_adopt_scoring(
    client: OvermindAPIClient, slug: str, project_id: str
) -> str:
    """Trigger a scoring job, or adopt an already-running one.

    Returns the job_id to wait on.
    """
    import httpx as _httpx
    import logging

    log = logging.getLogger(__name__)

    try:
        job = client.trigger_scoring(slug, project_id)
        log.info("Triggered new scoring job %s for '%s'", job["job_id"], slug)
        return job["job_id"]
    except _httpx.HTTPStatusError as exc:
        if (
            exc.response.status_code == 400
            and "already in progress" in exc.response.text
        ):
            log.info("Scoring job already running for '%s' — adopting it", slug)
            result = client.list_jobs(project_id=project_id, job_type="judge_scoring")
            for j in result.get("jobs", []):
                if j.get("status") in ("pending", "running"):
                    log.info(
                        "Adopted existing scoring job %s for '%s'", j["job_id"], slug
                    )
                    return j["job_id"]
            raise AssertionError(
                f"Backend said scoring is in progress for '{slug}' but no "
                f"pending/running job found. Jobs: {result.get('jobs', [])}"
            ) from exc
        raise


def test_trigger_scoring(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Trigger judge scoring for each discovered agent — must succeed for all."""
    project_id = shared_state.get("project_id")
    prompt_slugs = shared_state.get("prompt_slugs", {})
    assert project_id, "Run stage 1 first"
    assert len(prompt_slugs) == EXPECTED_AGENT_COUNT

    job_ids = []
    for slug in prompt_slugs:
        job_id = _trigger_or_adopt_scoring(overmind_client, slug, project_id)
        job_ids.append((slug, job_id))

    assert len(job_ids) == EXPECTED_AGENT_COUNT, (
        f"Expected {EXPECTED_AGENT_COUNT} scoring jobs, created {len(job_ids)}"
    )

    for slug, job_id in job_ids:
        completed = wait_for_job_completion(
            overmind_client, job_id, timeout_s=600, interval_s=10
        )
        assert completed["status"] in ("completed", "partially_completed"), (
            f"Scoring job for '{slug}' (id={job_id}) ended with "
            f"'{completed['status']}'. Result: {completed.get('result')}"
        )

    shared_state["scoring_job_ids"] = job_ids


def test_spans_have_scores(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify that each agent has at least MIN_SCORED_SPANS_PER_AGENT scored spans."""
    project_id = shared_state.get("project_id")
    prompt_slugs = shared_state.get("prompt_slugs", {})
    assert project_id
    assert prompt_slugs

    for slug in prompt_slugs:

        def _check(s=slug):
            detail = overmind_client.get_agent_detail(s, project_id)
            scored = detail.get("analytics", {}).get("scored_spans", 0)
            if scored >= MIN_SCORED_SPANS_PER_AGENT:
                return detail
            return None

        detail = poll_until(
            _check,
            timeout_s=120,
            interval_s=10,
            description=f"agent '{slug}' to have {MIN_SCORED_SPANS_PER_AGENT}+ scored spans",
        )

        scored = detail["analytics"]["scored_spans"]
        assert scored >= MIN_SCORED_SPANS_PER_AGENT, (
            f"Agent '{slug}': expected >= {MIN_SCORED_SPANS_PER_AGENT} scored spans, "
            f"got {scored}. Analytics: {detail.get('analytics')}"
        )

        avg_score = detail["analytics"].get("avg_score")
        assert avg_score is not None, (
            f"Agent '{slug}': avg_score is None. Analytics: {detail.get('analytics')}"
        )
        assert 0.0 <= avg_score <= 1.0, (
            f"Agent '{slug}': avg_score={avg_score} out of [0, 1] range"
        )
