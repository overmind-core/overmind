"""
Stage 7: Prompt improvement — trigger prompt tuning and verify that a
new prompt version is created with improved scores.

Requires:
- 30+ scored spans (stage 2, re-scored in stage 6).
- Strict criteria from review (stage 5) that penalise the original
  suboptimal QA prompt.
- Re-scored spans from stage 6 that show poor performance under strict
  criteria.

The tuning pipeline should generate an improved prompt that addresses
the strict criteria (funny jokes, not dry factual statements) and score higher.

Expected outcomes:
- Tuning job completes successfully.
- A new prompt version (v2+) is created.
- The comparison test shows a positive score_delta.
- A suggestion is created for the improved prompt.
"""

import logging

import httpx
import pytest

from helpers.api_client import OvermindAPIClient
from helpers.polling import wait_for_job_completion

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.stage_tuning]


def _pick_primary_agent(
    client: OvermindAPIClient, project_id: str, prompt_slugs: dict
) -> str:
    """Pick the agent with the most mapped spans (the QA agent)."""
    best_slug = None
    best_count = -1
    for slug in prompt_slugs:
        detail = client.get_agent_detail(slug, project_id)
        total = detail.get("analytics", {}).get("total_spans", 0)
        if total > best_count:
            best_count = total
            best_slug = slug
    assert best_slug, f"No agent with spans found. Slugs: {list(prompt_slugs.keys())}"
    logger.info("Selected agent '%s' (%d spans) for tuning", best_slug, best_count)
    return best_slug


def test_trigger_prompt_tuning(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Trigger prompt tuning for the primary agent (QA agent)."""
    project_id = shared_state.get("project_id")
    prompt_slugs = shared_state.get("prompt_slugs", {})
    assert project_id, "Run stage 1 first"
    assert prompt_slugs, "Run stage 3 first"

    slug = _pick_primary_agent(overmind_client, project_id, prompt_slugs)

    try:
        job = overmind_client.trigger_tuning(slug, project_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400 and "already in progress" in str(exc):
            existing = _find_running_tuning_job(overmind_client, project_id, slug)
            assert existing, (
                f"API says a tuning job is in progress for '{slug}' "
                f"but could not find it. Error: {exc}"
            )
            logger.info(
                "Found existing tuning job %s (status=%s), reusing it",
                existing["job_id"],
                existing["status"],
            )
            shared_state["tuning_job_id"] = existing["job_id"]
            shared_state["tuning_slug"] = slug
            return
        raise

    assert job["status"] == "pending", (
        f"Tuning job for '{slug}' should be pending, got '{job['status']}'"
    )
    shared_state["tuning_job_id"] = job["job_id"]
    shared_state["tuning_slug"] = slug


def _find_running_tuning_job(
    client: OvermindAPIClient, project_id: str, slug: str
) -> dict | None:
    """Find an existing pending/running tuning job for the given slug."""
    for status in ("running", "pending"):
        result = client.list_jobs(
            project_id=project_id,
            job_type="prompt_tuning",
            status=status,
        )
        for job in result.get("jobs", []):
            if job.get("prompt_slug") == slug:
                return job
    return None


def test_tuning_job_completes(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Wait for the tuning job to complete — must not fail."""
    job_id = shared_state.get("tuning_job_id")
    assert job_id, "test_trigger_prompt_tuning must run first"

    completed = wait_for_job_completion(
        overmind_client, job_id, timeout_s=900, interval_s=15
    )
    assert completed["status"] in ("completed", "partially_completed"), (
        f"Tuning job ended with '{completed['status']}'. "
        f"Result: {completed.get('result')}"
    )
    shared_state["tuning_result"] = completed.get("result")


def test_new_prompt_version_created(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify a new prompt version was created by tuning.

    After strict criteria from review (stage 5), the original QA prompt
    should score poorly, and the improved prompt should produce funny
    one-liner jokes — producing a positive score_delta.
    """
    slug = shared_state.get("tuning_slug")
    assert slug, "test_trigger_prompt_tuning must run first"

    result = shared_state.get("tuning_result", {})
    comparison = result.get("comparison_test", {})
    metrics = comparison.get("metrics", {})

    improvement = metrics.get("improvement", {})
    score_delta = improvement.get("score_delta")
    if score_delta is None:
        score_delta = metrics.get("score_delta")

    logger.info(
        "Tuning metrics: improvement=%s, full metrics=%s",
        improvement,
        metrics,
    )

    assert score_delta is not None, (
        f"Tuning result missing score_delta. "
        f"Result keys: {list(result.keys())}, "
        f"comparison keys: {list(comparison.keys())}, "
        f"metrics: {metrics}"
    )

    old_score = metrics.get("old_prompt", {}).get("avg_score")
    new_score = metrics.get("new_prompt", {}).get("avg_score")
    logger.info(
        "Comparison: old_prompt avg_score=%.4f, new_prompt avg_score=%.4f, delta=%.4f",
        old_score or 0,
        new_score or 0,
        score_delta,
    )

    assert score_delta > 0, (
        f"Expected positive score_delta for our deliberately bad prompt "
        f"with strict criteria, got {score_delta}. "
        f"old_prompt={old_score}, new_prompt={new_score}. "
        f"Full metrics: {metrics}"
    )

    project_id = shared_state.get("project_id")
    detail = overmind_client.get_agent_detail(slug, project_id)
    latest_version = detail.get("latest_version", 1)
    assert latest_version > 1, (
        f"Expected new prompt version (score_delta={score_delta}), "
        f"but latest version is {latest_version}. "
        f"Agent detail keys: {list(detail.keys())}"
    )


def test_tuning_suggestion_created(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify a suggestion was created for the improved prompt."""
    slug = shared_state.get("tuning_slug")
    project_id = shared_state.get("project_id")
    assert slug, "test_trigger_prompt_tuning must run first"

    detail = overmind_client.get_agent_detail(slug, project_id)
    suggestions = detail.get("suggestions", [])

    tuning_suggestions = [
        s
        for s in suggestions
        if "prompt" in s.get("title", "").lower()
        or "correctness" in s.get("title", "").lower()
    ]
    assert tuning_suggestions, (
        f"No prompt tuning suggestion found for agent '{slug}'. "
        f"All suggestions ({len(suggestions)}): "
        f"{[{'title': s.get('title'), 'type': s.get('type')} for s in suggestions]}"
    )
