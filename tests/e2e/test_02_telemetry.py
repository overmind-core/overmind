"""
Stage 2: Telemetry reception — send traces via the Overmind SDK, verify
they appear in the API.

Parametrized by LLM provider (openai, anthropic, gemini). Each variant
runs a QA agent (30 spans) and a tool agent (15 spans) through the
Overmind SDK with cached httpx transport.

Expected totals after this stage: 45 traces, 45 spans.
"""

import time

import pytest

from helpers.api_client import OvermindAPIClient
from helpers.llm_cache import LLMCache
from helpers.polling import poll_until
from mock_agents.qa_agent import QAAgent
from mock_agents.tool_agent import ToolAgent

pytestmark = [pytest.mark.e2e, pytest.mark.stage_telemetry]

EXPECTED_QA_QUERIES = 30
EXPECTED_TOOL_QUERIES = 15
EXPECTED_TOTAL_TRACES = EXPECTED_QA_QUERIES + EXPECTED_TOOL_QUERIES


@pytest.mark.parametrize("provider", ["gemini"])
def test_telemetry_qa_agent(
    provider: str,
    overmind_client: OvermindAPIClient,
    shared_state: dict,
    llm_cache: LLMCache,
    base_url: str,
):
    project_id = shared_state.get("project_id")
    api_token = shared_state.get("api_token")
    assert project_id, "Run stage 1 (onboarding) first"
    assert api_token, "Run stage 1 (onboarding) first"

    agent = QAAgent(
        provider=provider,
        api_token=api_token,
        cache=llm_cache,
        base_url=base_url,
    )
    success_count = agent.run()
    assert success_count == EXPECTED_QA_QUERIES, (
        f"QA agent: expected exactly {EXPECTED_QA_QUERIES} successful calls, "
        f"got {success_count}"
    )

    time.sleep(3)

    def _check_traces():
        result = overmind_client.list_traces(project_id, limit=1)
        total = result.get("count", 0)
        if total >= EXPECTED_QA_QUERIES:
            return total
        return None

    total = poll_until(
        _check_traces,
        timeout_s=60,
        interval_s=5,
        description=f"{EXPECTED_QA_QUERIES} QA traces ({provider}) to appear",
    )
    assert total >= EXPECTED_QA_QUERIES, (
        f"Expected at least {EXPECTED_QA_QUERIES} traces after QA agent, got {total}"
    )


@pytest.mark.parametrize("provider", ["gemini"])
def test_telemetry_tool_agent(
    provider: str,
    overmind_client: OvermindAPIClient,
    shared_state: dict,
    llm_cache: LLMCache,
    base_url: str,
):
    project_id = shared_state.get("project_id")
    api_token = shared_state.get("api_token")
    assert project_id, "Run stage 1 (onboarding) first"
    assert api_token, "Run stage 1 (onboarding) first"

    agent = ToolAgent(
        provider=provider,
        api_token=api_token,
        cache=llm_cache,
        base_url=base_url,
    )
    success_count = agent.run()
    assert success_count == EXPECTED_TOOL_QUERIES, (
        f"Tool agent: expected exactly {EXPECTED_TOOL_QUERIES} successful calls, "
        f"got {success_count}"
    )

    time.sleep(3)

    def _check_traces():
        result = overmind_client.list_traces(project_id, limit=1)
        total = result.get("count", 0)
        if total >= EXPECTED_TOTAL_TRACES:
            return total
        return None

    total = poll_until(
        _check_traces,
        timeout_s=60,
        interval_s=5,
        description=f"{EXPECTED_TOTAL_TRACES} total traces to appear",
    )
    assert total >= EXPECTED_TOTAL_TRACES, (
        f"Expected at least {EXPECTED_TOTAL_TRACES} traces after both agents, "
        f"got {total}"
    )


def test_traces_queryable(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify all ingested traces are queryable."""
    project_id = shared_state.get("project_id")
    assert project_id

    result = overmind_client.list_traces(project_id, limit=5)
    count = result.get("count", 0)
    assert count >= EXPECTED_TOTAL_TRACES, (
        f"Expected at least {EXPECTED_TOTAL_TRACES} traces, got {count}. "
        f"Response keys: {list(result.keys())}"
    )

    traces = result.get("traces", [])
    assert len(traces) > 0, f"Traces list is empty despite count={count}"

    trace = traces[0]
    assert "TraceId" in trace, f"Trace missing TraceId. Keys: {list(trace.keys())}"


def test_spans_have_expected_attributes(
    overmind_client: OvermindAPIClient,
    shared_state: dict,
):
    """Verify ingested spans have the expected gen_ai attributes."""
    project_id = shared_state.get("project_id")
    assert project_id

    result = overmind_client.list_traces(project_id, limit=3)
    traces = result.get("traces", [])
    assert len(traces) >= 1, (
        f"Expected traces to inspect, got {len(traces)}. count={result.get('count')}"
    )

    for trace in traces:
        trace_id = trace["TraceId"]
        detail = overmind_client.get_trace(trace_id, project_id)
        spans = detail.get("spans", [])
        assert spans, f"Trace {trace_id} has no spans"

        for span in spans:
            assert span.get("Inputs"), (
                f"Span {span.get('SpanId')} in trace {trace_id} missing Inputs. "
                f"Span keys: {list(span.keys())}"
            )
            assert span.get("Outputs"), (
                f"Span {span.get('SpanId')} in trace {trace_id} missing Outputs. "
                f"Span keys: {list(span.keys())}"
            )
