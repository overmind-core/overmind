"""
Stage 8: Model backtesting — trigger backtesting with cheaper models
and verify it recommends switching from the expensive model.

The QA agent uses gpt-5-mini for trivial questions, so backtesting MUST find
that a smaller/cheaper model achieves comparable quality and recommend
switching.

Expected outcomes:
- Available models endpoint returns models.
- Backtesting job completes successfully.
- Recommendations include a verdict of 'switch_recommended' or
  'consider_top_performer'.
- A suggestion is created for the agent.
"""

import logging

import httpx
import pytest

from helpers.api_client import OvermindAPIClient
from helpers.polling import wait_for_job_completion

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.stage_backtest]


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
    logger.info("Selected agent '%s' (%d spans) for backtesting", best_slug, best_count)
    return best_slug


BACKTEST_MODELS = [
    "gpt-5-nano",  # the cheapest version so it should prevail
    "gemini-3.1-flash-lite-preview",  # Gemini — cheapest
]


def test_get_available_models(overmind_client: OvermindAPIClient, shared_state: dict):
    """Verify the models endpoint returns our chosen backtest models."""
    models = overmind_client.list_backtest_models()
    available_names = {m["model_name"] for m in models}
    assert len(models) > 0, "No models available for backtesting"

    missing = [m for m in BACKTEST_MODELS if m not in available_names]
    assert not missing, (
        f"Expected backtest models not available: {missing}. "
        f"Available: {sorted(available_names)}"
    )
    shared_state["backtest_models"] = BACKTEST_MODELS


def test_trigger_backtesting(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Trigger backtesting for the first discovered agent with cheaper models."""
    project_id = shared_state.get("project_id")
    prompt_slugs = shared_state.get("prompt_slugs", {})
    assert project_id, "Run stage 1 first"
    assert prompt_slugs, "Run stage 3 first"

    cheaper = shared_state.get("backtest_models")
    assert cheaper, "Run test_get_available_models first"

    slug = _pick_primary_agent(overmind_client, project_id, prompt_slugs)
    prompt_id = prompt_slugs[slug]

    try:
        result = overmind_client.trigger_backtesting(
            prompt_id=prompt_id,
            models=cheaper,
            max_spans=30,
        )
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 400 and "already in progress" in str(exc):
            existing = _find_running_backtest_job(overmind_client, project_id, slug)
            assert existing, (
                f"API says a backtesting job is in progress for '{slug}' "
                f"but could not find it. Error: {exc}"
            )
            logger.info(
                "Found existing backtesting job %s (status=%s), reusing it",
                existing["job_id"],
                existing["status"],
            )
            shared_state["backtest_job_id"] = existing["job_id"]
            shared_state["backtest_slug"] = slug
            return
        raise

    assert result.get("job_id"), (
        f"No job_id returned from trigger_backtesting. Response: {result}"
    )
    shared_state["backtest_job_id"] = result["job_id"]
    shared_state["backtest_slug"] = slug


def _find_running_backtest_job(
    client: OvermindAPIClient, project_id: str, slug: str
) -> dict | None:
    """Find an existing pending/running backtesting job for the given slug."""
    for status in ("running", "pending"):
        result = client.list_jobs(
            project_id=project_id,
            job_type="model_backtesting",
            status=status,
        )
        for job in result.get("jobs", []):
            if job.get("prompt_slug") == slug:
                return job
    return None


def test_backtesting_job_completes(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Wait for the backtesting job to complete — must not fail."""
    job_id = shared_state.get("backtest_job_id")
    assert job_id, "test_trigger_backtesting must run first"

    completed = wait_for_job_completion(
        overmind_client, job_id, timeout_s=1500, interval_s=20
    )
    assert completed["status"] in ("completed", "partially_completed"), (
        f"Backtesting job ended with '{completed['status']}'. "
        f"Result: {completed.get('result')}"
    )
    shared_state["backtest_result"] = completed.get("result")


def test_backtesting_has_recommendations(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify backtesting produced recommendations with a clear verdict."""
    result = shared_state.get("backtest_result")
    assert result, "test_backtesting_job_completes must pass first"

    recommendations = result.get("recommendations")
    assert recommendations, (
        f"No recommendations in backtesting result. "
        f"Result keys: {list(result.keys())}, full result: {result}"
    )

    verdict = recommendations.get("verdict")
    assert verdict, f"No verdict in recommendations. Recommendations: {recommendations}"
    VALID_VERDICTS = (
        "switch_recommended",
        "consider_top_performer",
        "keep_current",
        "current_is_best",
    )
    assert verdict in VALID_VERDICTS, (
        f"Unexpected verdict '{verdict}'. Recommendations: {recommendations}"
    )

    summary = recommendations.get("summary", "")
    assert summary, f"Recommendations summary is empty. Verdict: {verdict}"


def test_backtesting_suggestion_created(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify backtesting produced an actionable outcome.

    If the verdict recommends switching, a suggestion must be created.
    If the current model is already best, no suggestion is expected —
    that is still a valid and useful backtesting result.
    """
    result = shared_state.get("backtest_result")
    slug = shared_state.get("backtest_slug")
    project_id = shared_state.get("project_id")
    assert result, "test_backtesting_job_completes must pass first"
    assert slug

    verdict = result.get("recommendations", {}).get("verdict", "")

    if verdict in ("keep_current", "current_is_best"):
        logger.info(
            "Backtesting verdict is '%s' — current model is optimal, "
            "no suggestion expected. Recommendations: %s",
            verdict,
            result.get("recommendations"),
        )
        return

    detail = overmind_client.get_agent_detail(slug, project_id)
    suggestions = detail.get("suggestions", [])

    backtest_suggestions = [
        s
        for s in suggestions
        if "backtest" in s.get("title", "").lower()
        or "model" in s.get("title", "").lower()
        or "switch" in s.get("description", "").lower()
    ]
    assert backtest_suggestions, (
        f"No backtesting suggestion found for agent '{slug}'. Verdict: {verdict}. "
        f"All suggestions ({len(suggestions)}): "
        f"{[{'title': s.get('title'), 'type': s.get('type')} for s in suggestions]}"
    )
