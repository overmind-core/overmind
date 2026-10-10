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


@pytest.mark.xfail(
    strict=True,
    reason="overbae/services/pii is not called from any landing path; trace rows keep PII.",
)
def test_personal_data_in_traces_is_redacted_in_the_dataset(
    workshop, sample_agent, live_api, support_desk_llm, worker
):
    sample_agent.run(
        "Refund order 42 please, mail me at jane.doe@example.com",
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
    assert "jane.doe@example.com" not in json.dumps(rows)


@pytest.mark.xfail(
    strict=True,
    reason="A row from a two-capability trace is labelled with the first capability.",
)
def test_trace_rows_carry_the_capability_they_were_selected_for(
    workshop, sample_agent, live_api, support_desk_llm, worker
):
    sample_agent.run("Refund order 42 please", api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    answer = workshop.capability("answer")
    [execution] = workshop.call("query_task_executions", {"capability": "answer"})[
        "task_executions"
    ]
    created = workshop.call(
        "create_dataset_from_traces",
        {
            "name": "answers",
            "trace_ids": [execution["trace_id"]],
            "intent": "eval",
            "capability": answer["id"],
        },
    )
    drain(worker)
    rows = workshop.call(
        "query_dataset",
        {"dataset": created["dataset"]["id"], "sql": "select capability_id from t"},
    )["rows"]
    assert rows == [{"capability_id": answer["id"]}]
