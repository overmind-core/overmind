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
    [candidate] = run["runs"]
    assert workshop.read(candidate["resource"]["uri"])["status"] == "completed"

    frozen = next(
        c
        for c in workshop.call("inspect_dataset", {"dataset": dataset})["cells"]
        if c["id"] == run["cell"]["id"]
    )
    assert frozen["frozen"] is True
    assert frozen["version"] == "1.0"


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
    [execution] = workshop.call("query_traces", {"capability": "answer"})["traces"]
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
    [execution] = workshop.call(
        "query_traces",
        {"capability": slug, "all_spans": True, "span_type": "entry_point", "name": slug},
    )["traces"]
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
    assert row["input"]["ticket"] == ticket, row
    expected_score = execution["trajectory_score"]
    if expected_score is None:
        expected_score = execution["eval_score"]
    assert row["score"] == expected_score
    prefix = "You triage" if slug == "triage" else "You are a support agent."
    assert row["prompt_tokens"] + row["completion_tokens"] == sum(
        request.total_tokens for request in fake_llm.requests if request.system.startswith(prefix)
    )
    messages = row["messages"]
    assert [m["content"] for m in messages if m["role"] == "user"] == [
        ticket if slug == "triage" else f"[refund] {ticket}"
    ]
    if slug == "triage":
        assert row["output"] == "refund"
        assert row["tools"] is None
        assert messages[-1]["content"] == "refund"
    else:
        assert row["input"]["category"] == "refund"
        assert row["output"] == "Your refund is on its way. The order was delivered."
        assert row["tools"][0]["function"]["name"] == "lookup_order"
        assert any(m["role"] == "tool" for m in messages)


@pytest.mark.parametrize("languages", [[], ["eng"]])
def test_mcp_ocr_language_choice_survives_cli_upload_and_reuses_pages(
    workshop, cli, sample_agent, worker, languages
):
    from pathlib import Path

    path = Path(__file__).parents[1] / "fixtures/documents/scanned.pdf"
    datasets = []
    for number in range(2):
        draft = workshop.call(
            "start_dataset",
            {
                "brief": "Keep document evidence for OCR verification.",
                "name": f"OCR source {number}",
                "intent": "explore",
            },
        )["dataset"]["id"]
        workshop.call("update_dataset", {"dataset": draft, "ocr_languages": languages})
        uploaded = json.loads(
            cli.run(
                "dataset", "upload", str(path), "--dataset", draft, "--json", cwd=sample_agent.repo
            )
        )
        assert uploaded["id"] == draft
        drain(worker)
        detail = workshop.call("inspect_dataset", {"dataset": draft})
        assert detail["ocr_languages"] == languages
        assert detail["sources"][0]["extraction"]["ocr"]["pages_reused"] == number
        assert detail["sources"][0]["extraction"]["ocr"]["languages"] == ["eng"]
        assert detail["sources"][0]["extraction"]["ocr"]["requested_languages"] == languages
        datasets.append(draft)
    invalid = workshop.call_result(
        "update_dataset", {"dataset": datasets[0], "ocr_languages": ["not-installed"]}
    )
    assert invalid.isError
    workshop.call("update_dataset", {"dataset": datasets[0], "ocr_languages": []})
    assert workshop.call("inspect_dataset", {"dataset": datasets[0]})["ocr_languages"] == []
