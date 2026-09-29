import json
from datetime import UTC, datetime, timedelta

import pytest

from .fakes.vendors import (
    BraintrustAPI,
    GalileoAPI,
    LangfuseAPI,
    LangSmithAPI,
    support_desk_trace,
)
from .stack import drain

VENDORS = {
    "langfuse": (LangfuseAPI, {"LANGFUSE_PUBLIC_KEY": "pk-fake", "LANGFUSE_SECRET_KEY": "sk-fake"}),
    "langsmith": (LangSmithAPI, {"LANGSMITH_API_KEY": "ls-fake"}),
    "braintrust": (BraintrustAPI, {"BRAINTRUST_API_KEY": "bt-fake"}),
    "galileo": (GalileoAPI, {"GALILEO_API_KEY": "gl-fake"}),
}


@pytest.mark.parametrize("vendor", sorted(VENDORS))
def test_a_recorded_vendor_trace_lands_as_one_rooted_tree(
    vendor, cli, mcp_for, sample_agent, worker, fake_llm, monkeypatch
):
    api_class, credentials = VENDORS[vendor]
    api = api_class()
    trace_id, steps = support_desk_trace(datetime.now(UTC) - timedelta(hours=1))
    api.record(trace_id, steps)
    fake_llm.network.vendors.append(api)
    for env, value in credentials.items():
        monkeypatch.setenv(env, value)

    cli.scan(sample_agent)
    cli.sync(sample_agent)
    mcp = mcp_for(cli.project_key(sample_agent))
    answer = mcp.capability("answer")

    added = json.loads(
        cli.run("connector", "add", vendor, "--base-url", api.host, "--json", cwd=sample_agent.repo)
    )
    connector = added["id"]
    [source] = mcp.call(
        "inspect_connectors", {"connector": connector, "include_source_projects": True}
    )["connector"]["source_projects"]
    triage = mcp.capability("triage")
    mapping = {
        "source": "observation_name",
        "assignments": {"triage": triage["id"], "answer": answer["id"]},
    }
    proposed = mcp.call(
        "configure_connector",
        {
            "connector": connector,
            "source_project_id": source["id"],
            "lookback_days": 7,
            "capability_mapping": mapping,
        },
    )
    assert proposed["mapping_pending"] is True
    approved = mcp.call("configure_connector", {"connector": connector, "confirm_mapping": True})
    assert approved["mapping_pending"] is False

    mcp.call("sync_connector", {"connector": connector})
    drain(worker)

    roots = mcp.call("query_traces", {"limit": 10})["traces"]
    landed = 0
    for root in roots:
        spans = mcp.read(f"overmind://traces/{root['trace_id']}")["spans"]
        assert len([s for s in spans if s["parent_span_id"] is None]) == 1
        landed += len(spans)
    assert landed == len(steps)
    bound = {root["name"]: root["capability"] for root in roots}
    assert bound["triage"] == "triage"
    assert bound["answer"] == "answer"
    assert sum(root["total_tokens"] or 0 for root in roots) == sum(sum(s.tokens) for s in steps)
