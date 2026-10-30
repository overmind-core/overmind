import pytest

from .stack import drain


@pytest.fixture
def project(cli, mcp_for, sample_agent, worker):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    return mcp_for(cli.project_key(sample_agent))


def spans_of(mcp, trace_id: str) -> list[dict]:
    return mcp.call("query_traces", {"trace_id": trace_id, "all_spans": True, "limit": 100})[
        "traces"
    ]


def assert_one_rooted_tree(mcp, trace_id: str) -> dict:
    trace = mcp.read(f"overmind://traces/{trace_id}")
    spans = trace["spans"]
    ids = {span["span_id"] for span in spans}
    roots = [span for span in spans if span["parent_span_id"] is None]
    assert len(roots) == 1
    assert all(span["parent_span_id"] in ids for span in spans if span is not roots[0])
    return trace


def test_sdk_traces_arrive_as_one_rooted_tree_bound_to_capabilities(
    project, sample_agent, live_api, support_desk_llm, fake_llm, worker, rest_for, cli
):
    replies = sample_agent.run(
        "Refund order 42 please", api_url=live_api.url, llm_url=support_desk_llm
    )
    assert replies == ["Your refund is on its way. The order was delivered."]
    drain(worker)

    [root] = project.call("query_traces", {"limit": 100})["traces"]
    trace = assert_one_rooted_tree(project, root["trace_id"])
    assert trace["root"]["name"] == "handle_ticket"

    rows = spans_of(project, root["trace_id"])
    models = [row for row in rows if row["model"]]
    by_capability = {row["capability"] for row in rows if row["span_type"] == "llm_call"}
    assert by_capability == {"triage", "answer"}
    assert any(row["name"] == "lookup_order" and row["capability"] == "answer" for row in rows)
    for capability in by_capability:
        [match] = project.call("query_traces", {"capability": capability, "limit": 100})["traces"]
        assert match["trace_id"] == root["trace_id"]

    agent_tokens = sum(
        r.total_tokens for r in fake_llm.requests if r.url.startswith("http://fake-llm")
    )
    assert root["total_tokens"] == agent_tokens
    assert models
    rest = rest_for(cli.project_key(sample_agent))
    for slug in ("triage", "answer"):
        capability = project.capability(slug)["id"]
        [filtered] = project.call("query_traces", {"capability": capability})["traces"]
        assert filtered["span_id"] == root["span_id"]
        page = rest.request(
            "GET",
            "/api/traces/",
            params={
                "project": project.project()["id"],
                "capability": capability,
            },
        )
        assert [row["span_id"] for row in page["results"]] == [root["span_id"]]
        spans = project.call("query_traces", {"capability": capability, "all_spans": True})[
            "traces"
        ]
        assert spans and all(span["capability"] == slug for span in spans)
