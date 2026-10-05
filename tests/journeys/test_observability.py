import pytest

from .stack import drain

TICKETS = ("Refund order 42 please", "Where is order 42?")


@pytest.fixture
def synced(cli, mcp_for, rest_for, sample_agent, worker):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    key = cli.project_key(sample_agent)
    return mcp_for(key), rest_for(key)


def test_traces_are_scored_on_arrival_and_every_surface_reads_the_same_scores(
    synced, sample_agent, live_api, support_desk_llm, rubric_judges, worker
):
    mcp, rest = synced
    answer = mcp.capability("answer")

    sample_agent.run(*TICKETS, api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)

    over_mcp = {
        e["trace_id"]: e["success_score"]
        for e in mcp.call("query_task_executions", {"capability": "answer"})["task_executions"]
    }
    project = mcp.project()["id"]
    page = rest.request(
        "GET", "/api/task-executions/", params={"project": project, "capability": answer["id"]}
    )
    over_rest = {e["trace_id"]: e["success_score"] for e in page["results"]}
    assert len(over_mcp) == len(TICKETS)
    assert over_rest == over_mcp
    assert all(score < 1 for score in over_mcp.values())

    health = mcp.call("inspect_capability_health", {"capability": "answer"})
    assert health["live_trace_scores"]["traces_scored"] == len(TICKETS)
    assert all(
        row["avg_value"] < 1
        for row in health["live_trace_scores"]["evaluators"]
        if "success" in row["evaluator"]
    )


def test_a_silent_capability_is_named_with_the_fix_that_instruments_it(
    synced, sample_agent, live_api, support_desk_llm, rubric_judges, worker
):
    mcp, rest = synced
    agent = sample_agent.repo / "support_desk" / "agent.py"
    instrumented = '@overmind.run(capability="triage", capability_id=capability_id("triage"))\n'
    agent.write_text(agent.read_text().replace(instrumented, ""))

    def coverage() -> dict[str, int]:
        graph = rest.request("GET", "/api/agent/", params={"project": mcp.project()["id"]})
        return {c["slug"]: c["trace_count"] for c in graph["capabilities"]}

    sample_agent.run(TICKETS[0], api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    assert coverage() == {"triage": 0, "answer": 1}

    plan = mcp.call("get_instrumentation_plan", {"capability": "triage"})
    [placement] = plan["placements"]
    assert placement["target"]["qualname"] == "support_desk.agent.triage"
    fix = placement["required_scope"]
    source = agent.read_text()
    agent.write_text(source.replace("def triage(", f"{fix}\ndef triage(", 1))

    sample_agent.run(TICKETS[1], api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    assert coverage() == {"triage": 1, "answer": 2}
