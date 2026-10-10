import json

import pytest

from .datasets import evaluate, tickets, upload
from .stack import drain


def test_an_uploaded_eval_source_is_usable_and_use_freezes_its_version(
    workshop, cli, sample_agent, worker, tmp_path
):
    answer = workshop.capability("answer")
    dataset = upload(
        cli, sample_agent, tickets(tmp_path), "--intent", "eval", "--capability", answer["id"]
    )
    drain(worker)
    prepared = workshop.call("inspect_dataset", {"dataset": dataset})["active"]
    assert prepared["fits"]["ok"] is True

    ready = workshop.call("check_evaluation_readiness", {"dataset": dataset})
    assert ready["dataset"]["cell"]["fingerprint"] == prepared["fingerprint"]

    run = evaluate(workshop, dataset)
    drain(worker)
    assert run["cell"]["version"] == "1.0"
    assert run["cell"]["fingerprint"] == prepared["fingerprint"]
    assert workshop.read(run["resource"]["uri"])["status"] == "completed"

    frozen = next(
        c
        for c in workshop.call("inspect_dataset", {"dataset": dataset})["cells"]
        if c["id"] == run["cell"]["id"]
    )
    assert frozen["frozen"] is True
    assert frozen["version"] == "1.0"


def test_an_unreadable_file_is_refused_before_it_becomes_a_dataset(
    workshop, cli, sample_agent, tmp_path
):
    broken = tmp_path / "broken.jsonl"
    broken.write_bytes(b"\x00\x01garbage{not json\n")
    with pytest.raises(AssertionError, match="transfer_conflict"):
        upload(cli, sample_agent, broken, "--intent", "eval")

    assert workshop.call("list_datasets", {"search": "broken"})["datasets"] == []


def test_trace_landing_preserves_source_text_for_explicit_transformation(
    workshop, sample_agent, live_api, support_desk_llm, worker
):
    ticket = "Refund order 42 please, mail me at jane.doe@example.com"
    sample_agent.run(
        ticket,
        api_url=live_api.url,
        llm_url=support_desk_llm,
    )
    drain(worker)
    [execution] = workshop.call("query_task_executions", {"capability": "answer"})[
        "task_executions"
    ]
    created = workshop.call(
        "create_dataset_from_traces",
        {"name": "with pii", "trace_ids": [execution["trace_id"]], "intent": "eval"},
    )
    drain(worker)
    rows = workshop.call(
        "query_dataset", {"dataset": created["dataset"]["id"], "sql": "select * from t"}
    )["rows"]
    assert ticket in json.dumps(rows)


@pytest.mark.parametrize("surface", ["mcp", "rest"])
@pytest.mark.parametrize("slug", ["triage", "answer"])
@pytest.mark.parametrize("capture", [False, True])
@pytest.mark.parametrize("selection", ["ids", "filter"])
def test_trace_rows_carry_the_capability_they_were_selected_for(
    workshop,
    sample_agent,
    live_api,
    support_desk_llm,
    worker,
    cli,
    rest_for,
    surface,
    slug,
    capture,
    selection,
    fake_llm,
):
    ticket = "Refund order 42 please"
    if capture:
        agent = sample_agent.repo / "support_desk" / "agent.py"
        source = agent.read_text()
        for name in ("triage", "answer"):
            source = source.replace(
                f"def {name}(", f'@overmind.observe(ignore=["client"])\ndef {name}('
            )
        agent.write_text(source)
    sample_agent.run(ticket, api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    capability = workshop.capability(slug)
    executions = workshop.call("query_task_executions", {"capability": slug})["task_executions"]
    trace = workshop.read(f"overmind://traces/{executions[0]['trace_id']}")
    [execution] = [e for e in executions if e["unit_span_id"] != trace["root"]["span_id"]]
    arguments = {"name": slug, "intent": "eval", "capability": capability["id"]}
    source = {"trace_ids": [execution["trace_id"]]}
    if selection == "filter":
        source = {"filters": {"capability": capability["id"]}}
        arguments.pop("capability")
    if surface == "mcp":
        created = workshop.call("create_dataset_from_traces", {**arguments, **source})
        dataset = created["dataset"]["id"]
    else:
        dataset = rest_for(cli.project_key(sample_agent)).request(
            "POST",
            "/api/datasets/",
            json={**arguments, "project": workshop.project()["id"], "source": {"traces": source}},
        )["id"]
    drain(worker)
    [row] = workshop.call(
        "query_dataset",
        {"dataset": dataset, "sql": "select * from t"},
    )["rows"]
    assert row["capability_id"] == capability["id"]
    if capture:
        assert row["input"]["ticket"] == ticket, row
    else:
        assert row["input"] is None
        assert row["output"] is None
    assert row["score"] == execution["success_score"]
    prefix = "You triage" if slug == "triage" else "You are a support agent."
    assert row["prompt_tokens"] + row["completion_tokens"] == sum(
        request.total_tokens for request in fake_llm.requests if request.system.startswith(prefix)
    )
    messages = row["messages"]
    assert [m["content"] for m in messages if m["role"] == "user"] == [
        ticket if slug == "triage" else f"[refund] {ticket}"
    ]
    if slug == "triage":
        if capture:
            assert row["output"] == "refund"
        assert row["tools"] is None
        assert messages[-1]["content"] == "refund"
    else:
        if capture:
            assert row["input"]["category"] == "refund"
            assert row["output"] == "Your refund is on its way. The order was delivered."
        assert row["tools"][0]["function"]["name"] == "lookup_order"
        assert any(m["role"] == "tool" for m in messages)


def test_repeated_capability_invocations_refuse_to_publish_a_mixed_example(
    workshop, sample_agent, live_api, support_desk_llm, worker
):
    agent = sample_agent.repo / "support_desk" / "agent.py"
    agent.write_text(
        agent.read_text().replace(
            "return answer(client, ticket, triage(client, ticket))",
            'triage(client, "A different ticket")\n    return answer(client, ticket, triage(client, ticket))',
        )
    )
    sample_agent.run("Refund order 42 please", api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    [trace] = workshop.call("query_traces", {})["traces"]
    created = workshop.call(
        "create_dataset_from_traces",
        {
            "name": "Repeated triage",
            "trace_ids": [trace["trace_id"]],
            "capability": workshop.capability("triage")["id"],
        },
    )
    drain(worker)
    dataset = workshop.call("inspect_dataset", {"dataset": created["dataset"]["id"]})
    assert dataset["state"] == "error"
    assert "2 separate invocations" in dataset["error"]
    assert dataset["cells"] == []


def test_a_source_capability_filter_is_preserved_when_the_destination_differs(
    workshop, sample_agent, live_api, support_desk_llm, worker
):
    sample_agent.run("Refund order 42 please", api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    triage = workshop.capability("triage")
    answer = workshop.capability("answer")
    created = workshop.call(
        "create_dataset_from_traces",
        {
            "name": "Triage source for answer",
            "filters": {"capability": triage["id"]},
            "capability": answer["id"],
        },
    )
    drain(worker)
    dataset_id = created["dataset"]["id"]
    [row] = workshop.call(
        "query_dataset",
        {
            "dataset": dataset_id,
            "sql": "select capability_id, messages from t",
        },
    )["rows"]
    assert row["capability_id"] == triage["id"]
    assert row["messages"][-1]["content"] == "refund"
    assert (
        workshop.call("inspect_dataset", {"dataset": dataset_id})["capability"]["id"]
        == answer["id"]
    )
