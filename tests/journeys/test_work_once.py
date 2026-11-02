import json

import pytest

from .conftest import GOOD_REPLY
from .otlp import (
    RUN_SIGNALS,
    SWEEP_SPANS,
    SWEEP_TRAJECTORIES,
    SWEEP_WAIT,
    Trace,
    score_history,
    trace_spans,
)
from .stack import drain

pytestmark = pytest.mark.covers("work-once")


@pytest.fixture
def project(cli, mcp_for, rest_for, sample_agent, worker, judges):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    key = cli.project_key(sample_agent)
    return mcp_for(key), rest_for(key), key


def test_scored_spans_trajectories_and_drift_verdicts_are_never_judged_again(
    project, live_api, worker, fake_llm, beat, clock
):
    mcp, rest, key = project
    project_id = mcp.project()["id"]
    mcp.call("set_signal_subscription", {"signal_type": "plan_drift", "enabled": True})

    trace = Trace(mcp.capability("answer")["id"], GOOD_REPLY, tools=[True])
    trace.send(live_api.url, key)
    drain(worker)
    beat(RUN_SIGNALS)
    tool_id = trace.tools[0][0].hex()
    # The judges meet an expectation only on the good reply, so the tool step drifts.
    assert trace_spans(rest, project_id, trace)[tool_id]["eval_attributes"]["drift"] == 0.0

    judged = len(fake_llm.decisions)
    clock.shift(SWEEP_WAIT)
    beat(RUN_SIGNALS, SWEEP_SPANS, SWEEP_TRAJECTORIES)
    assert len(fake_llm.decisions) == judged

    trace.add_tool(True)
    trace.send(live_api.url, key)
    drain(worker)
    beat(RUN_SIGNALS)
    spans = trace_spans(rest, project_id, trace)
    assert spans[trace.tools[1][0].hex()]["eval_attributes"]["drift"] == 0.0
    assert spans[trace.root_id.hex()]["eval_attributes"]["trajectory"]["span_count"] == 2
    trajectories = [
        row for row in score_history(rest, project_id, trace) if row["source_type"] == "trajectory"
    ]
    assert len(trajectories) == 1

    judged = len(fake_llm.decisions)
    clock.shift(SWEEP_WAIT)
    beat(RUN_SIGNALS, SWEEP_SPANS, SWEEP_TRAJECTORIES)
    assert len(fake_llm.decisions) == judged


def test_cli_batch_lands_once_and_mcp_retains_all_sources(
    workshop, cli, sample_agent, worker, tmp_path
):
    paths = [tmp_path / "first.jsonl", tmp_path / "second.jsonl"]
    for index, path in enumerate(paths):
        path.write_text(json.dumps({"value": index}) + "\n")
    arguments = ["dataset", "upload", *(str(path) for path in paths), "--json"]
    uploaded = json.loads(cli.run(*arguments, cwd=sample_agent.repo))
    drain(worker)
    replay = json.loads(cli.run(*arguments, cwd=sample_agent.repo))
    assert replay["id"] == uploaded["id"]
    assert replay["transfer"]["id"] == uploaded["transfer"]["id"]
    inspected = workshop.call("inspect_dataset", {"dataset": uploaded["id"]})
    assert inspected["active"]["rows"] == 2
    assert inspected["cell_page"]["total"] == 1
    assert [source["filename"] for source in inspected["sources"]] == [path.name for path in paths]
    rows = workshop.call(
        "query_dataset",
        {"dataset": uploaded["id"], "sql": "SELECT value FROM t ORDER BY source_row"},
    )["rows"]
    assert rows == [{"value": 0}, {"value": 1}]
