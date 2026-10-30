import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes.vendors import LangfuseAPI, support_desk_trace

from overbae.models import ConnectorCredential

from .conftest import GOOD_REPLY
from .otlp import EXPECTATION, RUN_SIGNALS, Trace, score_history, trace_spans
from .stack import drain, drain_scoring, settle

pytestmark = pytest.mark.covers("observe")

TICKETS = ("Refund order 42 please", "Where is order 42?", "Is order 42 refunded?")


def test_observe(
    console,
    console_url,
    replay_recorder,
    cli,
    mcp_for,
    rest_for,
    sample_agent,
    live_api,
    support_desk_llm,
    judges,
    fake_llm,
    worker,
    beat,
    monkeypatch,
):
    copy_the_onboarding_prompt(console, console_url, live_api)

    cli.scan(sample_agent)
    cli.sync(sample_agent)
    key = cli.project_key(sample_agent)
    mcp, rest = mcp_for(key), rest_for(key)
    project = mcp.project()["id"]
    snapshot = mcp.project()["repository_snapshot"]
    assert snapshot["commit"] == sample_agent.commit()
    assert snapshot["dirty"] is False
    assert snapshot["scanned_at"]
    for slug, truth in sample_agent.truth["capabilities"].items():
        capability = mcp.capability(slug)
        assert capability["name"] == truth["name"]
        assert capability["model"] == truth["model"]
        assert capability["status"] == "current"
    keep_new_api_keys_out_of_session_replay(console, project, replay_recorder)

    sdk_traces_arrive_as_rooted_trees(
        mcp, rest, sample_agent, live_api, support_desk_llm, fake_llm, worker
    )
    name_the_silent_capability_and_its_fix(
        mcp, rest, project, sample_agent, live_api, support_desk_llm, worker
    )
    every_surface_reads_the_same_scores(mcp, rest, project)
    console.goto(f"/capabilities?projectId={project}")
    for truth in sample_agent.truth["capabilities"].values():
        console.sees(truth["name"])
    console.goto(f"/observability?projectId={project}")
    console.click("Signals")
    console.sees("Signal checks")
    console.click("Sessions")
    console.click("Traces")
    console.sees("handle_ticket")

    score_once_per_evidence(mcp, rest, project, key, live_api, fake_llm, worker)
    raise_deduplicated_signals(mcp, rest, project, key, live_api, worker, beat)
    score_spans_before_their_root(mcp, rest, project, key, live_api, worker)
    import_a_vendor_trace(mcp, cli, sample_agent, fake_llm, worker, monkeypatch)

    show_agent_changes_without_reload(console, mcp, project)
    show_activity_and_onboarding_after_reload(console, mcp, rest, project, worker)


def copy_the_onboarding_prompt(console, console_url, live_api):
    console._browser.call(
        console._page.context.grant_permissions, ["clipboard-read", "clipboard-write"]
    )
    console.press("Copy onboarding prompt")
    console.sees("Prompt copied")
    prompt = console._browser.call(console._page.evaluate, "navigator.clipboard.readText()")
    assert prompt == (
        f"Set me up on Overmind\n\nusing the API at {live_api.url.rstrip('/')}: "
        f"{console_url.rstrip('/')}/setup.md"
    )
    console.wait_until("""() => {
        const panel = document.querySelector('section[aria-label="Get started"]');
        return panel.querySelector('a[href$="/setup.md"]') &&
            [...panel.querySelectorAll('img')].every(img => img.complete && img.naturalWidth > 0);
    }""")
    console._browser.call(console._page.set_viewport_size, {"width": 390, "height": 844})
    console.wait_until("""() => {
        const panel = document.querySelector('section[aria-label="Get started"]');
        return panel.scrollWidth <= panel.clientWidth && panel.getBoundingClientRect().right <= innerWidth;
    }""")
    console.save_evidence("embedded-onboarding-mobile")
    console._browser.call(console._page.set_viewport_size, {"width": 1280, "height": 720})
    console._browser.call(lambda: console._page.get_by_role("tab", name="Data", exact=True).click())
    console.sees("Create dataset")
    console.wait_until("() => !document.querySelector('[role=dialog]')")
    console._browser.call(
        lambda: console._page.get_by_role("tab", name="Agent", exact=True).click()
    )
    console.sees("Copy onboarding prompt")


def keep_new_api_keys_out_of_session_replay(console, project, replay_recorder):
    console.goto(f"/projects/{project}?projectId={project}")
    console.press("New API key")
    console._browser.call(lambda: console._page.add_script_tag(path=str(replay_recorder)))
    console._browser.call(
        console._page.evaluate,
        """() => {
            window.replayEvents = [];
            window.stopReplay = ReplayRecorder.record({
                emit: event => window.replayEvents.push(event),
                blockClass: 'ph-no-capture', maskTextClass: 'ph-mask', maskAllInputs: true
            });
        }""",
    )
    console.press("Create API key")
    console.sees("Your new API key:")
    console.wait_until("() => window.replayEvents.some(event => event.type === 3)")
    leaked = console._browser.call(
        console._page.evaluate,
        """() => {
            ReplayRecorder.record.takeFullSnapshot();
            const key = document.querySelector('[role="dialog"] code').textContent;
            const leaked = JSON.stringify(window.replayEvents).includes(key);
            window.stopReplay();
            return leaked;
        }""",
    )
    console.press("Done")
    console.wait_until("() => !document.querySelector('[role=\"dialog\"]')")
    assert leaked is False, "Session replay captured the API key"


def sdk_traces_arrive_as_rooted_trees(
    mcp, rest, sample_agent, live_api, support_desk_llm, fake_llm, worker
):
    replies = sample_agent.run(TICKETS[0], api_url=live_api.url, llm_url=support_desk_llm)
    assert replies == ["Your refund is on its way. The order was delivered."]
    drain(worker)

    [root] = mcp.call("query_traces", {"limit": 100})["traces"]
    trace = mcp.read(f"overmind://traces/{root['trace_id']}")
    ids = {span["span_id"] for span in trace["spans"]}
    [tree_root] = [span for span in trace["spans"] if span["parent_span_id"] is None]
    assert all(span["parent_span_id"] in ids for span in trace["spans"] if span is not tree_root)
    assert trace["root"]["name"] == "handle_ticket"

    rows = mcp.call(
        "query_traces", {"trace_id": root["trace_id"], "all_spans": True, "limit": 100}
    )["traces"]
    assert {row["capability"] for row in rows if row["span_type"] == "llm_call"} == {
        "triage",
        "answer",
    }
    assert any(row["name"] == "lookup_order" and row["capability"] == "answer" for row in rows)
    assert any(row["model"] for row in rows)
    agent_tokens = sum(
        r.total_tokens for r in fake_llm.requests if r.url.startswith("http://fake-llm")
    )
    assert root["total_tokens"] == agent_tokens
    for slug in ("triage", "answer"):
        capability = mcp.capability(slug)["id"]
        [filtered] = mcp.call("query_traces", {"capability": capability})["traces"]
        assert filtered["span_id"] == root["span_id"]
        page = rest.request(
            "GET", "/api/traces/", params={"project": mcp.project()["id"], "capability": capability}
        )
        assert [row["span_id"] for row in page["results"]] == [root["span_id"]]
        spans = mcp.call("query_traces", {"capability": capability, "all_spans": True})["traces"]
        assert spans and all(span["capability"] == slug for span in spans)


def name_the_silent_capability_and_its_fix(
    mcp, rest, project, sample_agent, live_api, support_desk_llm, worker
):
    agent = sample_agent.repo / "support_desk" / "agent.py"
    instrumented = '@overmind.run(capability="triage", capability_id=capability_id("triage"))\n'
    agent.write_text(agent.read_text().replace(instrumented, ""))

    def coverage() -> dict[str, int]:
        graph = rest.request("GET", "/api/agent/", params={"project": project})
        return {c["slug"]: c["trace_count"] for c in graph["capabilities"]}

    sample_agent.run(TICKETS[1], api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    assert coverage() == {"triage": 1, "answer": 2}

    plan = mcp.call("get_instrumentation_plan", {"capability": "triage"})
    [placement] = plan["placements"]
    assert placement["target"]["qualname"] == "support_desk.agent.triage"
    fix = placement["required_decorator"]
    agent.write_text(agent.read_text().replace("def triage(", f"{fix}\ndef triage(", 1))

    sample_agent.run(TICKETS[2], api_url=live_api.url, llm_url=support_desk_llm)
    drain_scoring(worker)
    assert coverage() == {"triage": 2, "answer": 3}


def every_surface_reads_the_same_scores(mcp, rest, project):
    over_mcp = {
        row["trace_id"]: (row["eval_score"], row["trajectory_score"])
        for row in mcp.call("query_traces", {"limit": 10})["traces"]
    }
    page = rest.request("GET", "/api/traces/", params={"project": project})
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
    assert (
        mcp.call("inspect_capability_health", {"capability": "answer"})["scoring"]["pass_rate"] < 1
    )


def score_once_per_evidence(mcp, rest, project, key, live_api, fake_llm, worker):
    answer = mcp.capability("answer")
    good = Trace(answer["id"], GOOD_REPLY, tools=[True])
    bad = Trace(answer["id"], "Your refund is on its way.", tools=[True])
    good.send(live_api.url, key)
    bad.send(live_api.url, key)
    drain_scoring(worker)

    roots = {
        row["trace_id"]: row
        for row in mcp.call("query_traces", {"capability": "answer", "limit": 20})["traces"]
    }
    assert roots[good.hex]["eval_score"] > roots[bad.hex]["eval_score"]
    assert roots[good.hex]["trajectory_score"] > roots[bad.hex]["trajectory_score"]

    root = trace_spans(rest, project, good)[good.root_id.hex()]
    expectation = root["eval_attributes"]["metrics"][EXPECTATION]
    assert expectation["verdict"] == "yes"
    assert expectation["score"] == 1.0
    assert root["eval_attributes"]["score"] == roots[good.hex]["eval_score"]

    first = score_history(rest, project, good)
    assert {row["source_type"] for row in first} == {"span", "trajectory"}
    judged = len(fake_llm.decisions)
    good.send(live_api.url, key)
    drain_scoring(worker)
    assert score_history(rest, project, good) == first
    assert len(fake_llm.decisions) == judged

    good.reply = "Your refund is on its way."
    good.send(live_api.url, key)
    drain_scoring(worker)
    assert len(score_history(rest, project, good)) > len(first)

    health = mcp.call("inspect_capability_health", {"capability": "answer"})
    assert health["scoring"]["scored"] >= 2
    assert any(m["metric"] == EXPECTATION for m in health["scoring"]["metrics"])


def raise_deduplicated_signals(mcp, rest, project, key, live_api, worker, beat):
    checks = mcp.call("list_signals", {})["signals"]
    assert next(check for check in checks if check["signal_type"] == "tool_failure")["enabled"]

    failing = Trace(mcp.capability("answer")["id"], GOOD_REPLY, tools=[False, False, True])
    failing.send(live_api.url, key)
    drain_scoring(worker)
    beat(RUN_SIGNALS)
    beat(RUN_SIGNALS)

    events = rest.request(
        "GET",
        "/api/signal-events/",
        params={"project": project, "unseen": "true", "signal_type": "tool_failure"},
    )["results"]
    assert [(e["signal_type"], e["trace_id"]) for e in events] == [("tool_failure", failing.hex)]
    assert "lookup_order (2/3)" in events[0]["message"]

    assert mcp.call("list_signals", {})["unseen_events"] >= 1
    assert events[0]["id"] in [e["id"] for e in mcp.read("overmind://signal-events")["events"]]
    evidence = mcp.call("query_traces", {"signal_event": events[0]["id"]})
    assert [trace["trace_id"] for trace in evidence["traces"]] == [failing.hex]

    rest.request("POST", "/api/signal-events/mark-seen/", json={"project": project})
    assert mcp.call("list_signals", {})["unseen_events"] == 0


def score_spans_before_their_root(mcp, rest, project, key, live_api, worker):
    trace = Trace(mcp.capability("answer")["id"], GOOD_REPLY, tools=[True, False])
    trace.send(live_api.url, key, root=False)
    drain_scoring(worker)
    tool_ids = [span_id.hex() for span_id, _ in trace.tools]
    before = {
        span_id: span["eval_attributes"]
        for span_id, span in trace_spans(rest, project, trace).items()
    }
    assert set(before) == set(tool_ids)
    assert all("score" in attributes for attributes in before.values())
    assert score_history(rest, project, trace)

    trace.send(live_api.url, key, tools=False)
    drain_scoring(worker)
    spans = trace_spans(rest, project, trace)
    assert {span_id: spans[span_id]["eval_attributes"] for span_id in tool_ids} == before
    assert spans[trace.root_id.hex()]["eval_attributes"]["trajectory"]["span_count"] == 3
    trajectories = [
        row for row in score_history(rest, project, trace) if row["source_type"] == "trajectory"
    ]
    assert len(trajectories) == 1


def import_a_vendor_trace(mcp, cli, sample_agent, fake_llm, worker, monkeypatch):
    api = LangfuseAPI()
    trace_id, steps = support_desk_trace(datetime.now(UTC) - timedelta(hours=1))
    api.record(trace_id, steps)
    fake_llm.network.vendors.append(api)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-fake")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-fake")
    before = {root["trace_id"] for root in mcp.call("query_traces", {"limit": 100})["traces"]}

    added = json.loads(
        cli.run(
            "connector", "add", "langfuse", "--base-url", api.host, "--json", cwd=sample_agent.repo
        )
    )
    connector = added["id"]
    [source] = mcp.call(
        "inspect_connectors", {"connector": connector, "include_source_projects": True}
    )["connector"]["source_projects"]
    mapping = {
        "source": "observation_name",
        "assignments": {
            "triage": mcp.capability("triage")["id"],
            "answer": mcp.capability("answer")["id"],
        },
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

    roots = [
        root
        for root in mcp.call("query_traces", {"limit": 100})["traces"]
        if root["trace_id"] not in before
    ]
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
    # The chunk schedules its own continuation; an inactive connector stops it.
    ConnectorCredential.objects.filter(pk=connector).update(is_active=False)


def show_agent_changes_without_reload(console, mcp, project):
    console.goto(f"/datasets?projectId={project}")
    console.sees("Datasets", exact=False)
    console._browser.call(console._page.evaluate, "window.activityDocument = 'unchanged'")
    dataset = mcp.call(
        "start_dataset",
        {"name": "Live agent source", "brief": "Review live agent changes", "intent": "explore"},
    )["dataset"]["id"]
    console.sees("Live agent source")
    mcp.call("update_dataset", {"dataset": dataset, "name": "Renamed live source"})
    console.sees("Renamed live source")
    console.wait_until(
        "() => !document.body.textContent.includes('Live agent source') && window.activityDocument === 'unchanged'"
    )
    console._browser.call(lambda: console._page.context.set_offline(True))
    mcp.call("update_dataset", {"dataset": dataset, "name": "Reconnected live source"})
    console._browser.call(lambda: console._page.context.set_offline(False))
    console.sees("Reconnected live source")
    console.wait_until("() => window.activityDocument === 'unchanged'")
    console.press("unread notification")
    console.sees("Reconnected live source", exact=False)
    console.save_evidence("agent-live-notifications")


def show_activity_and_onboarding_after_reload(console, mcp, rest, project, worker):
    activity = mcp.read("overmind://activity")
    assert {item["kind"] for item in activity["results"]} >= {
        "agent_connected",
        "repository_synced",
        "capabilities_available",
    }
    before = activity["count"]
    for _ in range(2):
        mcp.call("list_datasets")
    assert mcp.read("overmind://activity")["count"] == before

    console.goto(f"/?projectId={project}")
    mcp.call(
        "create_eval_matrix_row",
        {"name": "Visible activity check", "type": "llm", "source": "The answer is grounded."},
    )
    console.press("unread notification")
    console.sees("Visible activity check")
    console.press("Mark read")
    settle(worker, lambda: mcp.read("overmind://activity")["unseen_count"] == 0)

    console.goto(f"/?projectId={project}")
    console.press("Notifications")
    console.click("Visible activity check")
    console.wait_until("() => location.pathname.startsWith('/evaluations')")

    onboarding_run = str(uuid.uuid4())
    progress = mcp.call(
        "report_onboarding_progress", {"run_id": onboarding_run, "completed_steps": 2}
    )
    reporter = progress["milestones"][2]["actor_name"]
    assert reporter
    console.goto(f"/?projectId={project}")
    console.sees("Onboarding · Inspect repository")
    console.sees("2 of 10")
    console._browser.call(
        lambda: (
            console._page.get_by_label("Onboarding status", exact=True)
            .get_by_text(reporter, exact=True)
            .wait_for()
        )
    )
    mcp.call("report_onboarding_progress", {"run_id": onboarding_run, "completed_steps": 10})
    console.wait_until("() => !document.querySelector('[aria-label=\"Onboarding status\"]')")
    console.save_evidence("onboarding-complete")
