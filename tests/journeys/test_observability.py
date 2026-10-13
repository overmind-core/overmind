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
    synced, sample_agent, live_api, support_desk_llm, judges, worker
):
    mcp, rest = synced
    sample_agent.run(*TICKETS, api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)

    over_mcp = {
        row["trace_id"]: (row["eval_score"], row["trajectory_score"])
        for row in mcp.call("query_traces", {"limit": 10})["traces"]
    }
    page = rest.request("GET", "/api/traces/", params={"project": mcp.project()["id"]})
    over_rest = {
        row["trace_id"]: (
            row["eval_attributes"]["score"],
            row["eval_attributes"]["trajectory"]["score"],
        )
        for row in page["results"]
    }
    assert len(over_mcp) == len(TICKETS)
    assert over_rest == over_mcp
    assert all(trajectory < 1 for _, trajectory in over_mcp.values())

    failures = mcp.call("query_failures", {"capability": "answer"})
    assert {f["trace_id"] for f in failures["failures"]} == set(over_mcp)
    assert mcp.call("inspect_capability_health", {})["scoring"]["trajectories"] == len(TICKETS)
    health = mcp.call("inspect_capability_health", {"capability": "answer"})
    assert health["scoring"]["pass_rate"] < 1


def test_a_silent_capability_is_named_with_the_fix_that_instruments_it(
    synced, sample_agent, live_api, support_desk_llm, judges, worker
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
    fix = placement["required_decorator"]
    source = agent.read_text()
    agent.write_text(source.replace("def triage(", f"{fix}\ndef triage(", 1))

    sample_agent.run(TICKETS[1], api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    assert coverage() == {"triage": 1, "answer": 2}
