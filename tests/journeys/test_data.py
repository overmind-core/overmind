import hashlib
import json
from pathlib import Path

from overbae.models import Dataset
from overbae.services.datasets import land

from .datasets import evaluate, tickets, upload
from .stack import drain, settle

TICKET = "Refund order 42 please, mail me at jane.doe@example.com"
SCANNED = Path(__file__).parents[1] / "fixtures/documents/scanned.pdf"


def test_data(
    console,
    workshop,
    cli,
    sample_agent,
    rest_for,
    live_api,
    support_desk_llm,
    fake_llm,
    worker,
    tmp_path,
):
    hand_uploaded_data_to_the_coding_agent(console, tmp_path)

    project = workshop.project()["id"]
    rest = rest_for(workshop.api_key)
    use_freezes_an_uploaded_eval_source(console, workshop, project, worker, tmp_path)
    show_upload_bytes_until_publication(console, workshop, rest, project, worker)
    keep_the_brief_when_the_cli_attaches_data(
        console, workshop, cli, sample_agent, project, worker, tmp_path
    )

    sample_agent.run(TICKET, api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)
    land_selected_traces_as_rows(workshop, fake_llm, worker)
    open_traces_selected_in_the_console(console, workshop, project, live_api, worker)

    read_a_scanned_document_once_per_page(workshop, cli, sample_agent, worker)
    publish_version_bound_analytics(workshop, cli, sample_agent, rest, worker, tmp_path)
    keep_one_preparation_until_the_agent_finishes(
        console, workshop, cli, sample_agent, project, worker, tmp_path
    )


def hand_uploaded_data_to_the_coding_agent(console, tmp_path):
    console.goto("/projects")
    console.press("New project")
    console._browser.call(
        lambda: console._page.get_by_label("Project name", exact=True).fill("Data first")
    )
    console.upload('input[type="file"]', tickets(tmp_path))
    console.sees("3 rows")
    console.press("Create dataset")
    console.sees("Prepare your training data")
    dataset = Dataset.objects.get(name="Data first")
    assert dataset.intent == "train"
    assert dataset.brief == (
        "Explore the uploaded data and determine how best to prepare it for training."
    )
    assert dataset.capability_id is None
    console.wait_until(f"() => location.pathname.includes('/datasets/{dataset.id}')")
    console._browser.call(
        lambda: console._page.evaluate("""() => {
        window.copiedOnboardingPrompt = '';
        navigator.clipboard.write = async items => {
            window.copiedOnboardingPrompt = await (await items[0].getType('text/plain')).text();
        };
        navigator.clipboard.writeText = async text => { window.copiedOnboardingPrompt = text; };
    }""")
    )
    console.press("Copy onboarding prompt")
    console.wait_until("() => window.copiedOnboardingPrompt.length > 0")
    prompt = console._browser.call(lambda: console._page.evaluate("window.copiedOnboardingPrompt"))
    assert prompt.startswith(
        f"Set me up on Overmind with my uploaded data\n(project_id: {dataset.project_id},\ndataset_id: {dataset.id})"
    )
    assert prompt.endswith("/setup.md")
    assert "api-key" not in prompt.lower()
    console.save_evidence("data-onboarding-handoff")
    console._browser.call(
        lambda: (
            console._page.get_by_role("dialog")
            .get_by_role("button", name="Close", exact=True)
            .last.click()
        )
    )
    console.wait_until("() => !location.search.includes('prepare=')")


def use_freezes_an_uploaded_eval_source(console, workshop, project, worker, tmp_path):
    console.goto(f"/datasets?projectId={project}")
    console.command("New dataset")
    console.fill("#dataset-name", "Console upload")
    console.upload('[role="dialog"] input[type="file"]', tickets(tmp_path))
    console.sees("3 rows")
    console.choose("#dataset-purpose", "Evaluation")
    console.press("Create dataset")

    def landed():
        for dataset in workshop.call("list_datasets", {})["datasets"]:
            if dataset["name"] == "Console upload":
                inspected = workshop.call("inspect_dataset", {"dataset": dataset["id"]})
                return inspected if inspected["active"] else None
        return None

    inspected = settle(worker, landed)
    dataset = inspected["id"]
    assert inspected["active"]["rows"] == 3
    assert inspected["intent"] == "eval"
    assert inspected["active"]["fits"]["ok"] is True

    ready = workshop.call("check_evaluation_readiness", {"dataset": dataset})
    assert ready["dataset"]["cell"]["fingerprint"] == inspected["active"]["fingerprint"]
    run = evaluate(workshop, dataset)
    drain(worker)
    assert run["cell"]["version"] == "1.0"
    assert run["cell"]["fingerprint"] == inspected["active"]["fingerprint"]
    [candidate] = run["runs"]
    assert workshop.read(candidate["resource"]["uri"])["status"] == "completed"
    frozen = next(
        c
        for c in workshop.call("inspect_dataset", {"dataset": dataset})["cells"]
        if c["id"] == run["cell"]["id"]
    )
    assert frozen["frozen"] is True
    assert frozen["version"] == "1.0"


def show_upload_bytes_until_publication(console, workshop, rest, project, worker):
    dataset = workshop.call(
        "start_dataset", {"name": "Live upload", "brief": "Inspect uploaded rows"}
    )["dataset"]["id"]
    console.goto(f"/datasets?projectId={project}")
    console.sees("Waiting for source")
    data = b'[{"value":1},{"value":2}]'
    receipt = rest.request(
        "POST",
        "/api/dataset-transfers/",
        json={
            "project": project,
            "dataset": dataset,
            "request_key": "live-upload",
            "filename": "rows.json",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        },
    )
    console.sees("Uploading", exact=False)
    rest.request(
        "PUT",
        f"/api/dataset-transfers/{receipt['id']}/chunk/?offset=0",
        data=data[:10],
        headers={"Content-Type": "application/octet-stream"},
    )
    console.wait_until(
        "() => Number(document.querySelector('[role=progressbar]')?.getAttribute('aria-valuenow')) > 0"
    )
    rest.request(
        "PUT",
        f"/api/dataset-transfers/{receipt['id']}/chunk/?offset=10",
        data=data[10:],
        headers={"Content-Type": "application/octet-stream"},
    )
    rest.request("POST", f"/api/dataset-transfers/{receipt['id']}/complete/", json={})
    drain(worker)
    console.sees("Ready")
    console.wait_until(
        "() => !document.body.textContent.includes('Uploading') && [...document.querySelectorAll('td')].some(cell => cell.textContent === '2')"
    )


def keep_the_brief_when_the_cli_attaches_data(
    console, workshop, cli, sample_agent, project, worker, tmp_path
):
    brief = "Classify refund requests; retain uncertain labels for review."
    console.goto(f"/datasets?projectId={project}")
    console.command("New dataset")
    console.fill("#dataset-name", "Brief before data")
    console.fill("#dataset-brief", brief)
    console.press("Create dataset")

    def draft():
        return next(
            (
                row
                for row in workshop.call("list_datasets", {})["datasets"]
                if row["name"] == "Brief before data"
            ),
            None,
        )

    dataset = settle(worker, draft)["id"]
    before = workshop.call("inspect_dataset", {"dataset": dataset})
    assert before["brief"] == brief and before["active"] is None
    assert upload(cli, sample_agent, tickets(tmp_path), "--dataset", dataset) == dataset
    drain(worker)
    after = workshop.call("inspect_dataset", {"dataset": dataset})
    assert after["brief"] == brief
    assert after["active"]["rows"] == 3
    assert after["intent"] == before["intent"]
    console.goto(f"/datasets/{dataset}?projectId={project}")
    console.sees("Brief before data")
    console.sees("Refund order 0 please", exact=False)


def land_selected_traces_as_rows(workshop, fake_llm, worker):
    triage = workshop.capability("triage")
    [execution] = workshop.call(
        "query_traces",
        {"capability": "triage", "all_spans": True, "span_type": "entry_point", "name": "triage"},
    )["traces"]
    created = workshop.call(
        "create_dataset_from_traces",
        {
            "name": "triage",
            "intent": "eval",
            "capability": triage["id"],
            "trace_ids": [execution["trace_id"]],
        },
    )
    drain(worker)
    [row] = workshop.call(
        "query_dataset", {"dataset": created["dataset"]["id"], "sql": "select * from t"}
    )["rows"]
    assert TICKET in json.dumps(row)
    assert row["capability_id"] == triage["id"]
    assert row["input"]["ticket"] == TICKET
    expected_score = execution["trajectory_score"]
    if expected_score is None:
        expected_score = execution["eval_score"]
    assert row["score"] == expected_score
    assert row["prompt_tokens"] + row["completion_tokens"] == sum(
        request.total_tokens
        for request in fake_llm.requests
        if request.system.startswith("You triage")
    )
    assert [m["content"] for m in row["messages"] if m["role"] == "user"] == [TICKET]
    assert row["output"] == "refund"
    assert row["tools"] is None
    assert row["messages"][-1]["content"] == "refund"


def open_traces_selected_in_the_console(console, workshop, project, live_api, worker):
    console.goto(f"/observability?projectId={project}")
    console.click("Traces")
    console.sees("handle_ticket")
    console._browser.call(
        lambda: console._page.get_by_role("checkbox", name="Select row", exact=True).first.click()
    )
    console.press("Add to dataset")
    console.fill("#dataset-name", "Selected trace source")
    console.sees("None")

    def create():
        with console._page.expect_response(
            lambda response: (
                response.request.method == "POST"
                and response.url.rstrip("/").endswith("/api/datasets")
            )
        ) as response:
            console._page.get_by_role("button", name="Create dataset", exact=True).click()
        return response.value.status, response.value.json()

    status, result = console._browser.call(create)
    assert status == 201, result

    def landed():
        datasets = workshop.call("list_datasets", {"search": "Selected trace source"})["datasets"]
        if not datasets:
            return None
        dataset = workshop.call("inspect_dataset", {"dataset": datasets[0]["id"]})
        return dataset if dataset["active"] else None

    dataset = settle(worker, landed)
    assert dataset["active"]["rows"] == 1
    assert dataset["source_kind"] == "traces"
    assert dataset["intent"] == "pending"
    assert dataset["capability"] is None
    console.wait_until(
        f"() => location.pathname === '/datasets/{dataset['id']}' "
        "&& !!document.querySelector('article [data-cell-frame]')"
    )
    console.sees("Intent pending", exact=False)
    console.press("Prepare with agent")
    prompt = console._browser.call(
        lambda: console._page.get_by_role("textbox", name="Preparation prompt").input_value()
    )
    for fact in (dataset["id"], project, dataset["active"]["fingerprint"], live_api.url):
        assert fact in prompt
    console.save_evidence("trace-workshop-prepare")

    [trace] = workshop.call("query_traces", {})["traces"]
    without_events = Dataset.objects.create(
        project_id=project,
        name="Trace landing without live events",
        source_kind="traces",
        state="landing",
    )
    console._browser.call(
        lambda: console._page.route("**/api/datasets/*/events/", lambda route: route.abort())
    )
    console.goto(f"/datasets/{without_events.id}?projectId={project}")
    console.sees("Reading source data")
    land.land_traces(without_events, {"trace_ids": [trace["trace_id"]]})
    console.wait_until("() => !!document.querySelector('article [data-cell-frame]')")
    console._browser.call(lambda: console._page.unroute("**/api/datasets/*/events/"))


def read_a_scanned_document_once_per_page(workshop, cli, sample_agent, worker):
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
        workshop.call("update_dataset", {"dataset": draft, "ocr_languages": ["eng"]})
        uploaded = json.loads(
            cli.run(
                "dataset",
                "upload",
                str(SCANNED),
                "--dataset",
                draft,
                "--json",
                cwd=sample_agent.repo,
            )
        )
        assert uploaded["id"] == draft
        drain(worker)
        detail = workshop.call("inspect_dataset", {"dataset": draft})
        assert detail["ocr_languages"] == ["eng"]
        ocr = detail["sources"][0]["extraction"]["ocr"]
        assert ocr["pages_reused"] == number
        assert ocr["languages"] == ["eng"]
        assert ocr["requested_languages"] == ["eng"]
        datasets.append(draft)
    invalid = workshop.call_result(
        "update_dataset", {"dataset": datasets[0], "ocr_languages": ["not-installed"]}
    )
    assert invalid.isError


def publish_version_bound_analytics(workshop, cli, sample_agent, rest, worker, tmp_path):
    source_file = tmp_path / "imbalanced.jsonl"
    source_file.write_text(
        "".join(json.dumps({"amount": i, "fraud": int(i == 1000)}) + "\n" for i in range(1001))
    )
    dataset = upload(cli, sample_agent, source_file, "--intent", "train")
    drain(worker)
    source = workshop.call("inspect_dataset", {"dataset": dataset})["active"]
    result = workshop.call(
        "import_dataset_version",
        {
            "dataset": dataset,
            "source_cell": source["id"],
            "source_fingerprint": source["fingerprint"],
            "name": "Fraud decisions",
            "request_key": "analytics-transform",
            "provenance": "Test fixture encodes the supplied fraud label without changing rows.",
            "imported_rows": [
                {
                    "source_row": i,
                    "decision": {
                        "state": json.dumps({"amount": i}),
                        "question": "Fraud?",
                        "kind": "choice",
                        "options": ["No", "Yes"],
                        "target_probabilities": [int(i != 1000), int(i == 1000)],
                        "target_semantics": "categorical_gold",
                    },
                }
                for i in range(1001)
            ],
        },
    )
    drain(worker)
    receipt = workshop.call("get_job", {"kind": "dataset_pipeline", "id": result["job"]["id"]})
    assert receipt["status"] == "completed", receipt
    detail = workshop.call("inspect_dataset", {"dataset": dataset})
    cell = next(cell for cell in detail["cells"] if cell["id"] == detail["active"]["id"])
    report = cell["analytics"]
    assert report["fingerprint"] == cell["fingerprint"]
    assert report["cell"] == cell["id"]
    assert report["families"][0]["counts"] == [1000, 1]
    assert report["rows_scanned"] == 1001
    assert (
        next(c for c in report["checks"] if c["id"] == "targets.class_balance")["status"]
        == "warning"
    )
    assert receipt["progress"]["analytics"]["fingerprint"] == report["fingerprint"]
    rest_cell = next(
        c
        for c in rest.request("GET", f"/api/datasets/{dataset}/")["cells"]
        if c["id"] == cell["id"]
    )
    assert rest_cell["analytics"] == report


def keep_one_preparation_until_the_agent_finishes(
    console, workshop, cli, sample_agent, project, worker, tmp_path
):
    dataset = workshop.call(
        "start_dataset",
        {"name": "Continuous preparation", "brief": "Prepare support rows", "intent": "train"},
    )["dataset"]["id"]
    console.goto(f"/datasets?projectId={project}")
    console.sees("Continuous preparation")
    args = {"dataset": dataset, "request_key": "continuous-preparation"}
    started = workshop.call("start_dataset_preparation", args)["preparation_workflow"]
    assert (
        workshop.call("start_dataset_preparation", args)["preparation_workflow"]["id"]
        == started["id"]
    )
    console.sees("Preparing")
    workflow = {"dataset": dataset, "preparation_id": started["id"]}
    assert workshop.call_result(
        "update_dataset_preparation", {**workflow, "state": "completed"}
    ).isError
    assert workshop.call_result(
        "start_dataset_preparation", {**args, "request_key": "another"}
    ).isError
    upload(cli, sample_agent, tickets(tmp_path), "--dataset", dataset)
    drain(worker)
    source = workshop.call("inspect_dataset", {"dataset": dataset})["active"]
    result = workshop.call(
        "import_dataset_version",
        {
            "dataset": dataset,
            "source_cell": source["id"],
            "source_fingerprint": source["fingerprint"],
            "name": "Prepared support",
            "request_key": "continuous-import",
            "provenance": "Journey creates source-bound support messages",
            "imported_rows": [
                {
                    "source_row": i,
                    "messages": [
                        {"role": "user", "content": f"Refund order {i}"},
                        {"role": "assistant", "content": "Your refund is ready."},
                    ],
                }
                for i in range(3)
            ],
        },
    )
    drain(worker)
    receipt = workshop.call("get_job", {"kind": "dataset_pipeline", "id": result["job"]["id"]})
    assert receipt["status"] == "completed", receipt
    console.sees("Preparing")
    current = workshop.call("inspect_dataset_workbench", {"dataset": dataset})
    assert current["preparation_workflow"]["state"] == "preparing"
    events = workshop.read("overmind://activity")["results"]
    preparation_events = [e for e in events if e["kind"] == "dataset_preparation"]
    assert len(preparation_events) == 1
    event_id = preparation_events[0]["id"]
    assert not [e for e in events if e["task_id"] == result["job"]["id"]]
    workshop.call("update_dataset_preparation", {**workflow, "state": "paused"})
    console.sees("Preparation paused")
    workshop.call("update_dataset_preparation", {**workflow, "state": "preparing"})
    console.sees("Preparing")
    output = workshop.call("inspect_dataset", {"dataset": dataset})["active"]["id"]
    completion = {**workflow, "state": "completed", "outputs": [output]}
    workshop.call("update_dataset_preparation", completion)
    workshop.call("update_dataset_preparation", completion)
    console.press("unread notification")
    console.wait_until(
        f"() => document.querySelector('[data-activity-id=\"{event_id}\"]')?.dataset.activityStatus === 'completed'"
    )
    console.sees("Ready")
    assert workshop.call_result(
        "update_dataset_preparation", {**workflow, "state": "preparing"}
    ).isError
