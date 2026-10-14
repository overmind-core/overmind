import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from .datasets import evaluate, prepare, tickets, training_rows, upload
from .stack import drain

DOCUMENTS = Path(__file__).resolve().parents[1] / "fixtures" / "documents"


def _versions(workshop, dataset) -> dict[str, dict]:
    return {
        v["version"]: v for v in workshop.call("inspect_dataset", {"dataset": dataset})["versions"]
    }


def _add(workshop, dataset, parent, title, script) -> dict:
    return workshop.call(
        "add_dataset_version",
        {"dataset": dataset, "parent": parent, "title": title, "script": script},
    )


def _events(live_api, key, dataset) -> list[dict]:
    seen = []
    with httpx.stream(
        "GET",
        f"{live_api.url}/api/datasets/{dataset}/events/",
        headers={"X-Api-Key": key},
        timeout=30,
    ) as response:
        for line in response.iter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[6:])
            if event["type"] == "replay.done":
                return seen
            seen.append(event)
    return seen


def test_an_agent_grows_a_tree_of_versions_and_training_uses_the_one_it_activated(
    workshop, cli, sample_agent, live_api, worker, sft, fake_modal, tmp_path
):
    answer = workshop.capability("answer")
    train = upload(
        cli,
        sample_agent,
        training_rows(tmp_path),
        "--intent",
        "train",
        "--capability",
        answer["id"],
    )
    evals = upload(
        cli, sample_agent, tickets(tmp_path), "--intent", "eval", "--capability", answer["id"]
    )
    drain(worker)
    prepare(workshop, evals, "eval")
    landed = workshop.call("inspect_dataset", {"dataset": train})
    assert landed["state"] == "idle"
    assert landed["active"]["version"] == "v0"
    assert [v["version"] for v in landed["versions"]] == ["v0"]

    tried = workshop.call(
        "try_dataset_script", {"dataset": train, "parent": "v0", "script": "df = df.head(10)"}
    )
    assert (tried["status"], tried["rows"]) == ("ok", 10)
    assert list(_versions(workshop, train)) == ["v0"]

    first = _add(workshop, train, "v0", "Ten rows", "df = df.head(10)")
    assert (first["status"], first["version"]["version"]) == ("ok", "v1")
    short = _add(workshop, train, "v1", "First eight", "df = df.head(8)")
    late = _add(workshop, train, "v1", "Last eight", "df = df.tail(8)")
    assert (short["version"]["version"], late["version"]["version"]) == ("v2", "v3")
    again = _add(workshop, train, "v1", "Same script, new title", "df = df.head(8)")
    assert again["version"]["version"] == "v2"

    failed = _add(workshop, train, "v1", "Broken", "df = df['no_such_column']")
    assert failed["status"] == "failed"
    assert "no_such_column" in failed["error"]
    tree = _versions(workshop, train)
    assert sorted(tree) == ["v0", "v1", "v2", "v3"]
    assert tree["v2"]["parent"] == tree["v3"]["parent"] == "v1"
    v1 = workshop.call("inspect_dataset", {"dataset": train, "version": "v1"})["selected"]
    assert v1["impact"]["rows_removed"] == 2

    compared = workshop.call("diff_dataset_versions", {"dataset": train, "from": "v2", "to": "v3"})
    assert compared["diff"]["tracked"] is True

    workshop.call(
        "activate_dataset_version", {"dataset": train, "version": "v3", "expected_active": "v0"}
    )
    workshop.call(
        "start_finetune",
        {
            "dataset": train,
            "eval_dataset": evals,
            "base_model": "Qwen/Qwen3-1.7B",
            "capability": "answer",
            "eval_model_before": False,
        },
    )
    used = _versions(workshop, train)
    assert used["v3"]["used"] is True
    assert used["v2"]["used"] is False

    added = [
        e
        for e in _events(live_api, cli.project_key(sample_agent), train)
        if e["type"] == "version_added"
    ]
    assert [e["cell_id"] for e in added] == [used[v]["id"] for v in ("v1", "v2", "v3")]


def test_a_pdf_lands_one_row_per_text_element_and_a_script_groups_it_by_page(
    workshop, cli, sample_agent, worker
):
    document = upload(cli, sample_agent, DOCUMENTS / "native.pdf")
    drain(worker)
    landed = workshop.call(
        "query_dataset",
        {
            "dataset": document,
            "sql": "select page, text, _overmind_document_id from t order by page",
        },
    )["rows"]
    assert [(r["page"], r["text"]) for r in landed] == [
        (1, "Invoice total: 1234.56 USD"),
        (2, "Refunds are issued within 30 days."),
    ]
    assert len({r["_overmind_document_id"] for r in landed}) == 1

    pages = _add(
        workshop,
        document,
        "v0",
        "One row per page",
        "df = df.groupby(['source_name', 'page'], as_index=False).agg(text=('text', '\\n'.join))",
    )
    assert pages["status"] == "ok", pages["error"]
    grouped = workshop.call(
        "query_dataset",
        {"dataset": document, "version": "v1", "sql": "select page from t order by page"},
    )["rows"]
    assert grouped == [{"page": 1}, {"page": 2}]


def test_a_slow_run_answers_running_and_a_full_project_queues_the_next(
    workshop, cli, sample_agent, worker, settings, tmp_path
):
    settings.WORKSHOP_PROJECT_CONCURRENCY = 1
    dataset = upload(cli, sample_agent, tickets(tmp_path), "--intent", "eval")
    drain(worker)

    def attempt(seconds: int) -> dict:
        return workshop.call(
            "try_dataset_script",
            {
                "dataset": dataset,
                "parent": "v0",
                "script": f"import time\ntime.sleep({seconds})\ndf = df.head(1)",
            },
        )

    with ThreadPoolExecutor(2) as pool:
        slow = pool.submit(attempt, 30)
        time.sleep(3)
        waiting = pool.submit(attempt, 1)
        assert (slow.result()["status"], waiting.result()["status"]) == ("running", "queued")

    finished = [attempt(30), attempt(1)]
    deadline = time.monotonic() + 120
    while any(r["status"] != "ok" for r in finished) and time.monotonic() < deadline:
        finished = [attempt(30), attempt(1)]
    assert [(r["status"], r["rows"]) for r in finished] == [("ok", 1), ("ok", 1)]


def test_an_uploaded_eval_file_is_prepared_evaluated_and_marked_used(
    workshop, cli, sample_agent, worker, tmp_path
):
    answer = workshop.capability("answer")
    dataset = upload(
        cli, sample_agent, tickets(tmp_path), "--intent", "eval", "--capability", answer["id"]
    )
    drain(worker)
    version = prepare(workshop, dataset, "eval")
    prepared = workshop.call("inspect_dataset", {"dataset": dataset})["active"]
    assert prepared["fits"]["ok"] is True

    ready = workshop.call("check_evaluation_readiness", {"dataset": dataset})
    assert ready["dataset"]["version"]["fits"] is True

    run = evaluate(workshop, dataset)
    drain(worker)
    assert run["version"]["version"] == version
    assert run["version"]["fingerprint"] == prepared["fingerprint"]
    assert workshop.read(run["resource"]["uri"])["status"] == "completed"
    assert _versions(workshop, dataset)[version]["used"] is True


def test_an_unreadable_file_is_refused_before_it_becomes_a_dataset(
    workshop, cli, sample_agent, tmp_path
):
    broken = tmp_path / "broken.jsonl"
    broken.write_bytes(b"\x00\x01garbage{not json\n")
    with pytest.raises(AssertionError, match="Line 1 is not valid JSON"):
        upload(cli, sample_agent, broken, "--intent", "eval")

    assert workshop.call("list_datasets", {"search": "broken"})["datasets"] == []


@pytest.mark.xfail(
    strict=True,
    reason="Trace landing does not redact personal data; trace rows keep it.",
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
        "create_dataset",
        {
            "name": "with pii",
            "source": {"kind": "traces", "trace_ids": [execution["trace_id"]]},
            "intent": "eval",
        },
    )
    drain(worker)
    rows = workshop.call(
        "query_dataset", {"dataset": created["dataset"]["id"], "sql": "select * from t"}
    )["rows"]
    assert "jane.doe@example.com" not in json.dumps(rows)


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
        "create_dataset",
        {
            "name": "answers",
            "source": {"kind": "traces", "trace_ids": [execution["trace_id"]]},
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


def test_a_version_lands_as_its_own_dataset_or_split_into_train_and_eval(
    workshop, cli, sample_agent, worker, rest_for, tmp_path
):
    source = upload(cli, sample_agent, tickets(tmp_path, n=6), "--intent", "eval")
    drain(worker)
    version = prepare(workshop, source, "eval")
    project = workshop.project()["id"]

    copy = rest_for(cli.project_key(sample_agent)).request(
        "POST",
        "/api/datasets/",
        json={
            "project": project,
            "name": "tickets copy",
            "source": {"version": {"dataset": source, "version": version}},
        },
    )
    split = workshop.call(
        "create_dataset",
        {
            "name": "tickets",
            "source": {"kind": "version", "dataset": source},
            "split": {"eval_percent": 50, "position": "head"},
        },
    )
    drain(worker)

    copied = workshop.call("inspect_dataset", {"dataset": copy["id"]})
    assert (copied["state"], copied["intent"], copied["source_kind"]) == ("idle", "eval", "version")
    assert copied["source"]["version"]["dataset"] == source
    assert copied["source"]["version"]["version"] == version
    assert copied["active"]["rows"] == 6
    train = workshop.call("inspect_dataset", {"dataset": split["dataset"]["id"]})
    evaluation = workshop.call("inspect_dataset", {"dataset": split["eval_dataset"]["id"]})
    assert (train["intent"], evaluation["intent"]) == ("train", "eval")
    assert train["active"]["rows"] + evaluation["active"]["rows"] == 6
    assert evaluation["contamination_report"]["eval_rows"] == evaluation["active"]["rows"]
    assert train["source"]["version"]["version"] == version


def _add_with(workshop, call, attachment) -> dict:
    return workshop.call(
        "add_dataset_version", {**call, "title": "Labels", "attachment": attachment}
    )


def test_labels_made_outside_overmind_merge_into_a_version_that_keeps_the_file(
    workshop, cli, sample_agent, worker, beat, clock, tmp_path
):
    dataset = upload(cli, sample_agent, tickets(tmp_path, n=6), "--intent", "eval")
    drain(worker)
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "source_row,label\n" + "".join(f"{i},{'refund' if i < 4 else 'other'}\n" for i in range(6))
    )
    attached = json.loads(
        cli.run("dataset", "attach", str(labels), "--json", cwd=sample_agent.repo)
    )
    assert attached["sha256"] == hashlib.sha256(labels.read_bytes()).hexdigest()
    assert attached["rows"] == 6
    script = "df = df.merge(attachment, on='source_row')\ndf['expected_output'] = df['label']"
    call = {"dataset": dataset, "parent": "v0", "script": script}

    tried = workshop.call("try_dataset_script", {**call, "attachment": attached["upload_id"]})
    assert (tried["status"], tried["rows"]) == ("ok", 6)
    added = _add_with(workshop, call, attached["upload_id"])
    assert added["status"] == "ok", added["error"]
    version = added["version"]
    assert version["attachment"]["sha256"] == attached["sha256"]
    [balance] = version["analytics"]["labels"]
    assert balance["field"] == "expected_output"
    assert balance["majority"] == {"label": "refund", "share": 0.6667}
    assert _add_with(workshop, call, attached["upload_id"])["version"]["id"] == version["id"]
    assert workshop.call("add_dataset_version", {**call, "title": "No file"})["status"] == "failed"

    clock.shift(25 * 3600)
    beat("overbae.tasks.cleanup_tmp.cleanup_uploads")
    with pytest.raises(AssertionError, match="attachment is missing"):
        workshop.call("try_dataset_script", {**call, "attachment": attached["upload_id"]})
    kept = httpx.get(
        f"{cli.api_url}/api/datasets/{dataset}/attachment/",
        params={"cell": version["version"]},
        headers={"X-Api-Key": cli.project_key(sample_agent)},
    )
    assert kept.content == labels.read_bytes()


def _running(rest, dataset) -> dict:
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        runs = rest.request("GET", f"/api/datasets/{dataset}/executions/")
        running = [run for run in runs if run["state"] == "running"]
        if running:
            return running[0]
        time.sleep(0.2)
    raise AssertionError("No run started.")


def test_a_cancelled_script_run_stops_frees_the_project_and_keeps_no_version(
    workshop, cli, sample_agent, worker, settings, rest_for, tmp_path
):
    settings.WORKSHOP_PROJECT_CONCURRENCY = 1
    dataset = upload(cli, sample_agent, tickets(tmp_path), "--intent", "eval")
    drain(worker)
    rest = rest_for(cli.project_key(sample_agent))
    slow = "import time\ntime.sleep(120)\ndf = df.head(1)"

    with ThreadPoolExecutor(1) as pool:
        pending = pool.submit(_add, workshop, dataset, "v0", "Slow", slow)
        run = _running(rest, dataset)
        started = time.monotonic()
        stopped = workshop.call("cancel_dataset_job", {"dataset": dataset, "run": run["id"]})
        assert (stopped["kind"], stopped["status"]) == ("dataset_run", "cancelled")
        assert pending.result()["status"] == "cancelled"

    quick = _add(workshop, dataset, "v0", "Two rows", "df = df.head(2)")
    assert quick["status"] == "ok"
    assert time.monotonic() - started < 30
    tree = _versions(workshop, dataset)
    assert sorted(tree) == ["v0", "v1"]
    assert tree["v1"]["title"] == "Two rows"


def test_a_stopped_import_lands_nothing_and_cannot_be_resumed(
    workshop, cli, sample_agent, worker, settings
):
    settings.SANDBOX_URL = "http://127.0.0.1:9"
    dataset = upload(cli, sample_agent, DOCUMENTS / "native.pdf")
    job = f"overmind://jobs/dataset_import/{dataset}"
    deadline = time.monotonic() + 30
    while workshop.read(job)["import"]["state"] != "running":
        assert time.monotonic() < deadline, "The import did not start."
        time.sleep(0.1)

    stopped = workshop.call("cancel_dataset_job", {"dataset": dataset})
    assert (stopped["kind"], stopped["status"]) == ("dataset_import", "stopped")
    drain(worker)

    landed = workshop.read(job)
    assert (landed["state"], landed["import"]["state"]) == ("stopped", "cancelled")
    assert landed["error"] is None
    detail = workshop.call("inspect_dataset", {"dataset": dataset})
    assert (detail["versions"], detail["next_actions"]) == ([], [])
    with pytest.raises(AssertionError, match="not blocked"):
        workshop.call("resume_dataset_import", {"dataset": dataset})
