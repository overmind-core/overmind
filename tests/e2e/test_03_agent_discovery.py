"""
Stage 3: Agent discovery — trigger template extraction and verify
that exactly 2 agents (prompt templates) are created from the ingested traces.

The QA agent and tool agent have deliberately different system prompts and
query formats so the template extractor MUST identify them as two distinct
prompt templates.  If only 1 (or 0) templates are found, that is a product
bug in the template extractor.

Requires 60 spans in the project (30 QA + 30 tool, from stage 2).
"""

import pytest

from helpers.api_client import OvermindAPIClient
from helpers.polling import wait_for_job_completion

pytestmark = [pytest.mark.e2e, pytest.mark.stage_discovery]

EXPECTED_AGENT_COUNT = 2


def test_trigger_agent_discovery(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Trigger agent discovery and wait for the job to complete.

    Agent discovery at this scale (60 spans) should finish within 120 seconds.
    """
    project_id = shared_state.get("project_id")
    assert project_id, "Run stage 1 first"

    job = overmind_client.trigger_extract_templates(project_id)
    assert job["job_type"] == "agent_discovery", (
        f"Expected job_type 'agent_discovery', got '{job['job_type']}'"
    )
    assert job["status"] in ("pending", "running", "completed"), (
        f"Expected status 'pending', 'running', or 'completed', got '{job['status']}'"
    )

    if job["status"] == "completed":
        completed_job = job
    else:
        completed_job = wait_for_job_completion(
            overmind_client, job["job_id"], timeout_s=120, interval_s=5
        )
    assert completed_job["status"] == "completed", (
        f"Agent discovery job ended with status '{completed_job['status']}' "
        f"(expected 'completed'). "
        f"Result: {completed_job.get('result')}"
    )
    shared_state["discovery_job_result"] = completed_job.get("result")


def test_exactly_two_agents_discovered(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify exactly 2 agents were discovered (QA template + tool template).

    No polling — after the discovery job completes, agents must be immediately
    visible.  If not exactly 2, dump full debug info.
    """
    project_id = shared_state.get("project_id")
    assert project_id

    agents_resp = overmind_client.list_agents(project_id)
    agents = agents_resp.get("data", [])

    assert len(agents) == EXPECTED_AGENT_COUNT, (
        f"Expected exactly {EXPECTED_AGENT_COUNT} agents, found {len(agents)}.\n"
        f"Discovery job result: {shared_state.get('discovery_job_result')}\n"
        f"Agents returned: {[{'slug': a.get('slug'), 'prompt_id': a.get('prompt_id'), 'version': a.get('version')} for a in agents]}\n"
        f"Full response keys: {list(agents_resp.keys())}"
    )

    for agent in agents:
        assert agent.get("slug"), f"Agent missing slug. Agent data: {agent}"
        assert agent.get("prompt_id"), f"Agent missing prompt_id. Agent data: {agent}"
        assert agent.get("version") >= 1, (
            f"Agent version should be >= 1, got {agent.get('version')}. "
            f"Agent: {agent.get('slug')}"
        )

    shared_state["prompt_slugs"] = {a["slug"]: a["prompt_id"] for a in agents}


def test_spans_mapped_to_agents(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify that spans are mapped to discovered prompts and counts are sane.

    Also identifies and stores the QA agent slug for downstream tests.
    Both agents have 30 spans, so we distinguish them by prompt text:
    the tool agent's template contains 'FUNCTION_EXECUTOR'.
    """
    project_id = shared_state.get("project_id")
    prompt_slugs = shared_state.get("prompt_slugs", {})
    assert project_id
    assert len(prompt_slugs) == EXPECTED_AGENT_COUNT, (
        f"Expected {EXPECTED_AGENT_COUNT} prompt slugs, got {len(prompt_slugs)}"
    )

    total_mapped_spans = 0
    qa_slug = None
    for slug in prompt_slugs:
        detail = overmind_client.get_agent_detail(slug, project_id)
        total_spans = detail.get("analytics", {}).get("total_spans", 0)
        assert total_spans > 0, (
            f"Agent '{slug}' has 0 mapped spans. Analytics: {detail.get('analytics')}"
        )
        total_mapped_spans += total_spans

        versions = detail.get("versions", [])
        prompt_text = versions[0].get("prompt_text", "") if versions else ""
        if "FUNCTION_EXECUTOR" not in (prompt_text or ""):
            qa_slug = slug

    assert total_mapped_spans >= 60, (
        f"Expected at least 60 total mapped spans across all agents, "
        f"got {total_mapped_spans}"
    )

    assert qa_slug, (
        f"Could not identify QA agent — no agent without 'FUNCTION_EXECUTOR' "
        f"in prompt text. Slugs: {list(prompt_slugs.keys())}"
    )
    shared_state["qa_agent_slug"] = qa_slug
