import json
import uuid

from modal_shared.training_monitoring import fingerprint
from overbae.models import Dataset, FinetuningJob
from overbae.services import training_monitoring

from ..datasets import tickets, upload
from ..stack import drain, settle


def test_the_console_shows_the_synced_agent_and_its_traces(
    console, cli, mcp_for, sample_agent, live_api, support_desk_llm, worker
):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    project = mcp_for(cli.project_key(sample_agent)).project()["id"]
    sample_agent.run("Refund order 42 please", api_url=live_api.url, llm_url=support_desk_llm)
    drain(worker)

    console.goto(f"/capabilities?projectId={project}")
    for truth in sample_agent.truth["capabilities"].values():
        console.sees(truth["name"])

    console.goto(f"/observability?projectId={project}")
    console.click("Root traces")
    console.sees("handle_ticket")


def test_a_file_uploaded_in_the_console_becomes_a_usable_dataset(
    console, cli, mcp_for, sample_agent, worker, tmp_path
):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    mcp = mcp_for(cli.project_key(sample_agent))
    project = mcp.project()["id"]

    console.goto(f"/datasets?projectId={project}")
    console.press("New dataset")
    console.fill("#dataset-name", "Console upload")
    console.upload('input[type="file"]', tickets(tmp_path))
    console.sees("3 rows")
    console.choose("#dataset-purpose", "Evaluation")
    console.press("Create dataset")

    def landed():
        for dataset in mcp.call("list_datasets", {})["datasets"]:
            if dataset["name"] == "Console upload":
                inspected = mcp.call("inspect_dataset", {"dataset": dataset["id"]})
                return inspected if inspected["active"] else None
        return None

    inspected = settle(worker, landed)
    assert inspected["active"]["rows"] == 3
    assert inspected["intent"] == "eval"


def test_a_console_brief_survives_its_first_cli_attachment(
    console, workshop, cli, sample_agent, worker, tmp_path
):
    project = workshop.project()["id"]
    brief = "Classify refund requests; retain uncertain labels for review."
    console.goto(f"/datasets?projectId={project}")
    console.press("New dataset")
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


def test_decision_data_selects_native_training_controls(
    console, workshop, cli, sample_agent, worker, tmp_path, rest_for
):
    project = workshop.project()["id"]
    path = tmp_path / "decision.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "decision": {
                        "state": f"Refund request {i}",
                        "question": "Approve refund?",
                        "kind": "noul",
                        "options": ["No", "Yes"],
                        "target_probabilities": [0.25, 0.75],
                        "target_semantics": "annotator_distribution",
                    }
                }
            )
            + "\n"
            for i in range(12)
        )
    )
    dataset = upload(cli, sample_agent, path, "--intent", "train")
    drain(worker)
    recommendation = rest_for(cli.project_key(sample_agent)).request(
        "POST", "/api/finetuning-jobs/recommend/", json={"dataset_id": dataset}
    )
    readiness = workshop.call("check_finetune_readiness", {"dataset": dataset})
    assert recommendation["task_type"] == readiness["task_type"] == "decision"
    assert (
        recommendation["task_type_source"] == readiness["task_type_source"] == "declared_contract"
    )
    assert recommendation["dataset"]["rows"] == 12
    assert recommendation["benchmark_snapshot"]["generated_at"] is None
    assert recommendation["candidates"] == readiness["recommendations"] == []
    assert readiness["dataset"]["validation"]["valid"] and readiness["n_candidates"] > 0
    console.goto(f"/training?projectId={project}&train=true&datasetId={dataset}")
    console.sees("Choose training data, model and evaluations.")
    console.wait_until("""() => document.querySelector(
        '[aria-label="Run pre-training baseline evaluation"]')?.getAttribute('data-state') === 'checked'""")
    console.sees("Measure base-model performance before training.")
    console.sees("Native probability training · Modal LoRA")
    console.sees("No benchmark data", exact=False)
    console.wait_until("""() => !document.body.innerText.includes("Can't be trained on yet")
        && !document.body.innerText.includes("Validating")""")
    assert not FinetuningJob.objects.filter(project_id=project).exists()


def test_native_monitoring_evidence_remains_readable_after_cancellation(console, workshop):
    project = workshop.project()["id"]
    group = uuid.uuid4()
    job = FinetuningJob.objects.create(
        project_id=project,
        dataset=Dataset.objects.create(project_id=project, name="Decision receipts"),
        base_model="Qwen/Qwen3-0.6B",
        status="running",
        group_id=group,
        hyperparameters={"objective": "decision_cross_entropy"},
    )
    examples = [
        {
            "row": 0,
            "input": "Refund order 42",
            "reference": [0.25, 0.75],
            "output": [0.2, 0.8],
            "status": "completed",
        }
    ]
    training_monitoring.ingest(
        job,
        {
            "checks": [
                {
                    "key": "1:development:4",
                    "attempt": 1,
                    "stream": "development",
                    "step": 4,
                    "state": "completed",
                    "policy_fingerprint": "a" * 64,
                    "sample_fingerprint": "b" * 64,
                    "coverage": {"scored": 4, "expected": 4},
                    "metrics": {
                        "eval_loss": 0.625,
                        "hard_label_accuracy": 0.75,
                        "distribution_decisions": 4,
                        "mean_decisions": 0,
                        "cross_entropy": 0.625,
                        "brier": 0.125,
                    },
                    "artifact": {"sha256": fingerprint(examples), "examples": examples},
                }
            ]
        },
    )
    console.goto(f"/training?projectId={project}&groupId={group}&jobId={job.pk}")
    console.sees("Development monitoring")
    console.sees("75.0%")
    console.press("Inspect step 4")
    console.sees("Distribution cross entropy")
    console.sees("0.6250")
    check = job.validation_runs.get()
    evidence = workshop.call(
        "inspect_training_progress", {"job": str(job.pk), "check": str(check.pk)}
    )
    assert evidence["progress"]["items"] == examples
    console.press("Cancel")
    console.sees("Requests cancellation. Completed checks and saved checkpoints remain available.")
    console.press("Cancel Job")
    console.sees("Cancelled")
    job.refresh_from_db()
    assert job.status == "cancelled"
    console.goto(f"/training?projectId={project}&groupId={group}&jobId={job.pk}")
    console.sees("Development monitoring")
    console.press("Inspect step 4")
    console.sees("Distribution cross entropy")
    assert (
        workshop.call("inspect_training_progress", {"job": str(job.pk), "check": str(check.pk)})[
            "progress"
        ]["items"]
        == examples
    )


def test_project_surfaces_render_without_browser_or_server_errors(console, workshop):
    project = workshop.project()["id"]
    for route, text in [
        ("datasets", "New dataset"),
        ("training", "Train model"),
        ("evaluations", "Evaluations"),
        ("inference", "Inference"),
        ("optimiser", "Optimiser"),
    ]:
        console.goto(f"/{route}?projectId={project}")
        console.sees(text)
        console.healthy()
        console.save_evidence(f"project-surface-{route}")
