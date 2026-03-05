"""
Stage 6: Re-score with strict criteria.

After review (stage 5) saved strict evaluation criteria (responses must be
funny jokes; plain factual statements score 0) the backend automatically
cleared existing correctness scores on all mapped spans.  This stage:

1. Triggers scoring — all 30 QA spans are now re-evaluated against the
   strict criteria.
2. Verifies the average score dropped below the initial ~1.0.

Expected outcomes:
- Scoring job completes for the primary agent.
- Average score across all spans is below 1.0.
"""

import logging

import pytest

from helpers.api_client import OvermindAPIClient
from helpers.polling import wait_for_job_completion

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.stage_rescore]


def test_trigger_rescoring(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Trigger scoring — all spans are now unscored after criteria change.

    Stage 5 changed criteria which cleared existing correctness scores.
    This job re-scores everything with the strict criteria.
    """
    project_id = shared_state.get("project_id")
    assert project_id, "Run stage 1 first"

    slug = shared_state.get("qa_agent_slug")
    assert slug, "QA agent slug not set — run stage 3 first"

    import httpx as _httpx

    try:
        job = overmind_client.trigger_scoring(slug, project_id)
        job_id = job.get("job_id")
        assert job_id, f"No job_id from trigger_scoring for '{slug}'. Response: {job}"
    except _httpx.HTTPStatusError as exc:
        if (
            exc.response.status_code == 400
            and "already in progress" in exc.response.text
        ):
            logger.info("Scoring already running for '%s' — adopting it", slug)
            result = overmind_client.list_jobs(
                project_id=project_id, job_type="judge_scoring"
            )
            running = [
                j
                for j in result.get("jobs", [])
                if j.get("status") in ("pending", "running")
            ]
            assert running, (
                f"Backend said scoring in progress for '{slug}' but no "
                f"pending/running job found."
            )
            job_id = running[0]["job_id"]
            logger.info("Adopted existing scoring job %s", job_id)
        else:
            raise

    completed = wait_for_job_completion(
        overmind_client, job_id, timeout_s=600, interval_s=10
    )
    assert completed["status"] in ("completed", "partially_completed"), (
        f"Re-scoring job for '{slug}' ended with '{completed['status']}'. "
        f"Result: {completed.get('result')}"
    )

    shared_state["rescore_job_id"] = job_id
    shared_state["rescore_slug"] = slug


def test_scores_dropped(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify that the agent's average score is now below the initial ~1.0.

    With strict criteria, the re-scored spans should pull the average
    down noticeably.
    """
    slug = shared_state.get("rescore_slug")
    project_id = shared_state.get("project_id")
    assert slug, "test_trigger_rescoring must pass first"

    detail = overmind_client.get_agent_detail(slug, project_id)
    analytics = detail.get("analytics", {})
    scored = analytics.get("scored_spans", 0)
    avg_score = analytics.get("avg_score")

    logger.info(
        "Agent '%s' after re-scoring: scored_spans=%s, avg_score=%s",
        slug,
        scored,
        avg_score,
    )

    assert avg_score is not None, (
        f"avg_score is None for '{slug}'. Analytics: {analytics}"
    )
    assert avg_score < 0.95, (
        f"Expected avg_score to drop below 0.95 after strict re-scoring, "
        f"got {avg_score}. The strict criteria (must be a funny joke, plain "
        f"factual statements score 0) should have penalised the dry one-sentence "
        f"responses. Analytics: {analytics}"
    )
    logger.info(
        "Score drop confirmed for '%s': avg_score=%.3f (scored %d spans)",
        slug,
        avg_score,
        scored,
    )
