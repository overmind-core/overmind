"""
Stage 5: Agent review — the user provides feedback on judge scoring to
tighten the evaluation criteria so that the originally-perfect-scoring
prompt now receives lower marks.

This MUST run before prompt tuning (stage 7) because without stricter
criteria, the trivial QA prompt scores 100 % and there is nothing to
improve.  By raising the bar here we ensure tuning can find meaningful
improvements.

Expected outcomes:
- Review endpoint returns worst and best spans.
- Negative feedback is submitted (judge scored too high; responses are
  plain facts without humor).
- Sync-refresh-description incorporates the feedback.
- **Strict criteria** are saved that require funny one-liner jokes —
  plain dry factual statements score 0.
- The initial_review_completed flag is True on the agent.
"""

import logging

import pytest

from helpers.api_client import OvermindAPIClient

logger = logging.getLogger(__name__)

pytestmark = [pytest.mark.e2e, pytest.mark.stage_review]

STRICT_CRITERIA = {
    "correctness": [
        "The response must be a funny one-liner joke that embeds the factual answer within the humor",
        "Plain, dry factual statements with no humor, wit, or comedic angle must be scored 0",
        "Simply restating the fact in a declarative sentence (e.g. 'The capital of France is Paris.') is not acceptable — there must be a clear joke or punchline",
    ]
}


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
    logger.info("Selected agent '%s' (%d spans) for review", best_slug, best_count)
    return best_slug


def test_get_review_spans(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Fetch worst and best spans for review — both must be non-empty."""
    project_id = shared_state.get("project_id")
    prompt_slugs = shared_state.get("prompt_slugs", {})
    assert project_id, "Run stage 1 first"
    assert prompt_slugs, "Run stage 3 first"

    slug = _pick_primary_agent(overmind_client, project_id, prompt_slugs)
    result = overmind_client.get_review_spans(slug, project_id)

    assert "worst_spans" in result, (
        f"Response missing 'worst_spans'. Keys: {list(result.keys())}"
    )
    assert "best_spans" in result, (
        f"Response missing 'best_spans'. Keys: {list(result.keys())}"
    )
    assert len(result["worst_spans"]) > 0 or len(result["best_spans"]) > 0, (
        f"Expected non-empty worst_spans or best_spans for agent '{slug}'"
    )

    assert result.get("agent_description"), (
        f"Missing agent_description for '{slug}'. Keys: {list(result.keys())}"
    )
    assert result.get("evaluation_criteria"), (
        f"Missing evaluation_criteria for '{slug}'. Keys: {list(result.keys())}"
    )

    shared_state["review_slug"] = slug
    shared_state["review_worst_spans"] = result["worst_spans"]
    shared_state["review_best_spans"] = result["best_spans"]
    shared_state["review_description"] = result["agent_description"]
    shared_state["review_criteria"] = result["evaluation_criteria"]


def test_submit_judge_feedback(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Submit negative feedback: the judge was too generous.

    For ALL reviewed spans (worst AND best) we say the score is too high
    because responses lack citations, confidence, and caveats.
    """
    worst = shared_state.get("review_worst_spans", [])
    best = shared_state.get("review_best_spans", [])
    assert worst or best, "No review spans available — test_get_review_spans must pass"

    negative_text = "Actually I want responses to be funny, like 1 sentence joke"
    feedback_map = {}

    for span in worst + best:
        sid = span["span_id"]
        resp = overmind_client.submit_span_feedback(
            span_id=sid,
            feedback_type="judge",
            rating="down",
            text=negative_text,
        )
        assert resp["rating"] == "down", (
            f"Feedback for span {sid}: expected rating 'down', got '{resp['rating']}'"
        )
        feedback_map[sid] = {"rating": "down", "text": negative_text}

    assert len(feedback_map) > 0, "Failed to submit any feedback"
    shared_state["review_feedback_map"] = feedback_map


def test_sync_refresh_description(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Trigger synchronous description refresh with inline feedback."""
    slug = shared_state.get("review_slug")
    project_id = shared_state.get("project_id")
    feedback_map = shared_state.get("review_feedback_map", {})
    assert slug, "test_get_review_spans must pass first"
    assert feedback_map, "test_submit_judge_feedback must pass first"

    span_ids = list(feedback_map.keys())

    result = overmind_client.sync_refresh_description(
        slug=slug,
        span_ids=span_ids,
        feedback=feedback_map,
        project_id=project_id,
    )

    assert result, f"Empty response from sync-refresh-description for '{slug}'"
    shared_state["refreshed_description"] = result


def test_update_description_and_criteria(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Save the LLM-refreshed description with deliberately STRICT criteria.

    The strict criteria require funny one-liner jokes — the original QA
    prompt produces plain factual statements that will score 0 under these
    rules.  This ensures subsequent scoring marks the agent poorly, giving
    prompt tuning room to improve.
    """
    slug = shared_state.get("review_slug")
    project_id = shared_state.get("project_id")
    refreshed = shared_state.get("refreshed_description", {})
    assert slug, "test_get_review_spans must pass first"

    description = refreshed.get("description") or shared_state.get(
        "review_description", "Updated agent description for E2E test"
    )

    result = overmind_client.update_description(
        slug=slug,
        description=description,
        criteria=STRICT_CRITERIA,
        project_id=project_id,
    )
    assert result.get("success") is True, (
        f"update_description failed for '{slug}'. Response: {result}"
    )

    scores_cleared = result.get("scores_cleared", 0)
    logger.info(
        "update_description cleared %d existing correctness scores for '%s'",
        scores_cleared,
        slug,
    )

    prompt_slugs = shared_state.get("prompt_slugs", {})
    prompt_id = prompt_slugs.get(slug)
    assert prompt_id, f"No prompt_id for slug '{slug}'"

    saved_criteria = overmind_client.get_prompt_criteria(prompt_id).get(
        "evaluation_criteria", {}
    )
    assert "correctness" in saved_criteria, (
        f"Strict criteria not persisted for '{slug}' (prompt {prompt_id}). "
        f"Got: {saved_criteria}"
    )
    assert len(saved_criteria["correctness"]) >= len(STRICT_CRITERIA["correctness"]), (
        f"Expected >= {len(STRICT_CRITERIA['correctness'])} correctness rules, "
        f"got {len(saved_criteria.get('correctness', []))}"
    )
    logger.info("Strict criteria saved for agent '%s': %s", slug, saved_criteria)


def test_mark_initial_review_complete(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Mark the initial review as completed."""
    slug = shared_state.get("review_slug")
    project_id = shared_state.get("project_id")
    assert slug, "test_get_review_spans must pass first"

    result = overmind_client.mark_initial_review_complete(slug, project_id)
    assert result.get("success") is True, (
        f"mark_initial_review_complete failed for '{slug}'. Response: {result}"
    )


def test_review_completed_flag_set(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify the initial_review_completed flag is True on the agent."""
    slug = shared_state.get("review_slug")
    project_id = shared_state.get("project_id")
    assert slug, "test_get_review_spans must pass first"

    detail = overmind_client.get_agent_detail(slug, project_id)
    agent_desc = detail.get("agent_description") or {}
    assert agent_desc.get("initial_review_completed") is True, (
        f"initial_review_completed not True for '{slug}'. "
        f"agent_description: {agent_desc}"
    )
