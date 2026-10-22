import json
import uuid

from django.utils import timezone

from modal_shared.training_monitoring import fingerprint
from overbae.models import (
    Cell,
    Dataset,
    DatasetContext,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    NativeEvaluationPlan,
)
from overbae.services import training_monitoring

from ..datasets import tickets, upload
from ..stack import drain, settle


def test_evaluations_share_runs_navigation_and_probability_metrics(console, workshop):
    project = workshop.project()["id"]
    dataset = Dataset.objects.create(
        project_id=project, name="Evaluation navigation fixture", intent="eval"
    )
    cell = Cell.objects.create(
        dataset=dataset, state="ok", position=0, title="Final suite", rows=10
    )
    Cell.objects.create(dataset=dataset, state="ok", position=1, title="Later data", rows=999)
    calibration_dataset = Dataset.objects.create(
        project_id=project, name="Calibration navigation fixture", intent="eval"
    )
    calibration_cell = Cell.objects.create(
        dataset=calibration_dataset, state="ok", position=0, title="Calibration suite", rows=3
    )
    EvalRun.objects.create(
        project_id=project, dataset=dataset, name="Response evaluation fixture", status="completed"
    )
    metric = {"mean": 1.8, "decisions": 7, "groups": 5}
    coverage = {
        "expected": 10,
        "scored": 7,
        "missing_predictions": 1,
        "invalid_predictions": 1,
        "incompatible_inputs": 1,
        "metrics": {"cross_entropy": metric},
    }
    plan = NativeEvaluationPlan.objects.create(
        project_id=project,
        final_cell=cell,
        calibration_cell=calibration_cell,
        name="Probability evaluation fixture",
        state="completed",
        config={
            "baseline": "base",
            "suites": {
                "final": {"cell": str(cell.pk), "rows": 10},
                "calibration": {"cell": str(calibration_cell.pk), "rows": 3},
            },
            "participants": [
                {"key": "base", "name": "Base", "model": "Qwen/Qwen3-0.6B", "kind": "external"},
                {
                    "key": "trained",
                    "name": "Trained",
                    "model": "Qwen/Qwen3-0.6B",
                    "kind": "external",
                },
            ],
        },
        calls={"score": {"state": "completed", "observed_at": timezone.now().isoformat()}},
        results={
            "comparisons": {
                "trained": {
                    "raw": {
                        "baseline": {"benchmarks": {"Soft targets": coverage}},
                        "candidate": {"benchmarks": {"Soft targets": coverage}},
                        "benchmarks": {
                            "Soft targets": {
                                "metrics": {
                                    "cross_entropy": {
                                        "candidate_minus_baseline": -0.1,
                                        "decisions": 7,
                                        "groups": 5,
                                    }
                                }
                            }
                        },
                    }
                }
            }
        },
    )
    console.goto(f"/evaluations?projectId={project}&view=decisions")
    console.sees("Response evaluation fixture")
    console.sees("Probability evaluation fixture")
    console.wait_until(
        """() => ![...document.querySelectorAll('[role=tab]')].some(el => el.textContent === 'Decisions')"""
    )
    console.click("Response evaluation fixture")
    console.sees("Re-run")
    console.sees("Optimise")
    console.goto(f"/evaluations?projectId={project}")
    console.click("Probability evaluation fixture")
    console.sees("Evaluation navigation fixture")
    console.sees("Calibration navigation fixture")
    console.wait_until(f"""() => {{
        const details = document.querySelector('[aria-label="Run details"]');
        return details && details.innerText.includes('Models')
            && details.innerText.includes('Baseline')
            && details.innerText.includes('10 rows')
            && details.innerText.includes('3 rows')
            && details.innerText.includes('Created')
            && details.innerText.includes('Completed')
            && !details.innerText.includes('999')
            && !details.innerText.includes('Judge')
            && [...details.querySelectorAll('a')].some(a =>
                a.href.includes('/datasets/{dataset.id}') && a.href.includes('cell={cell.id}'));
    }}""")
    console.sees("Cross entropy")
    console.sees("1.8000")
    console.sees("7 / 10")
    console._browser.call(
        lambda: console._page.get_by_role("button", name="Trained: 7 / 10 scored").focus()
    )
    console.sees("1 missing · 1 invalid · 1 incompatible")
    console.wait_until(f"() => location.pathname.endsWith('/runs/{plan.id}')")
    console.wait_until("""() => document.querySelectorAll('table:not([aria-busy=true])').length === 1
        && document.querySelector('[aria-label="Benchmark datapoints"]')
        && !document.body.innerText.includes('Download all metrics')
        && !document.body.innerText.includes('Frozen inputs, model identities and execution receipts')
        && !document.body.innerText.includes('Calibration, class and slice diagnostics')
        && !document.body.innerText.includes('Recorded costs and unreported components')
        && !document.body.innerText.includes('Measure workload')
        && getComputedStyle(document.querySelector('h1')).fontFamily.includes('Mondwest')
        && getComputedStyle(document.querySelector('h2')).fontFamily.includes('Geist Pixel')""")
    console.save_evidence("evaluation-probability-metrics")
    console.goto(f"/evaluations?projectId={project}")
    console.press("New evaluation")
    console.click("Model probabilities")
    console.sees("Final suite", exact=False)
    console.sees("Calibration suite", exact=False)
    console.sees("Baseline", exact=False)
    console.sees("Save draft")
    console.wait_until(
        """() => getComputedStyle(document.querySelector('[role=dialog] h2')).fontFamily.includes('Geist Pixel')"""
    )
    assert not console.page_errors
    assert not console.server_errors


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
    console.click("Signals")
    console.sees("Signal checks")
    console.click("Sessions")
    console.click("Traces")
    console.sees("handle_ticket")


def test_a_file_uploaded_in_the_console_becomes_a_usable_dataset(
    console, cli, mcp_for, sample_agent, worker, tmp_path
):
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    mcp = mcp_for(cli.project_key(sample_agent))
    project = mcp.project()["id"]

    console.goto(f"/datasets?projectId={project}")
    console.command("New dataset")
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
    assert recommendation["task_type"] == readiness["task_type"] == "unknown"
    assert recommendation["task_type_source"] == readiness["task_type_source"] == "unknown"
    assert recommendation["dataset"]["rows"] == 12
    assert recommendation["benchmark_snapshot"]["generated_at"] is None
    assert recommendation["candidates"] == readiness["recommendations"] == []
    assert readiness["dataset"]["validation"]["valid"] and readiness["n_candidates"] > 0
    DatasetContext.objects.update_or_create(
        dataset_id=dataset,
        defaults={
            "project_id": project,
            "task_type": "classification",
            "task_type_source": "semantic",
            "extracted_at": timezone.now(),
        },
    )
    recommendation = rest_for(cli.project_key(sample_agent)).request(
        "POST", "/api/finetuning-jobs/recommend/", json={"dataset_id": dataset}
    )
    readiness = workshop.call("check_finetune_readiness", {"dataset": dataset})
    assert recommendation["task_type"] == readiness["task_type"] == "classification"
    assert readiness["training_contract"]["inference_contract"] == "decision"
    assert "Qwen/Qwen3-0.6B" in {model for tier in readiness["catalog"].values() for model in tier}
    assert "google/gemma-4-31B-it" not in {
        model for tier in readiness["catalog"].values() for model in tier
    }
    console.goto(f"/training?projectId={project}&train=true&datasetId={dataset}")
    console.sees("Choose training data, model and evaluations.")
    console.wait_until("""() => document.querySelector(
        '[aria-label="Run pre-training baseline evaluation"]')?.getAttribute('data-state') === 'checked'""")
    console.sees("Measure base-model performance before training.")
    console.sees("Native probability training · Modal LoRA")
    console.sees("Training mode")
    console.sees("Decision", exact=True)
    console.sees("Classification", exact=True)
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
    console.sees("Development loss")
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
    console.sees("Development loss")
    console.sees("0.6250")
    assert (
        workshop.call("inspect_training_progress", {"job": str(job.pk), "check": str(check.pk)})[
            "progress"
        ]["items"]
        == examples
    )


def test_project_surfaces_render_without_browser_or_server_errors(console, workshop):
    project = workshop.project()["id"]
    for route, text in [
        ("datasets", "Datasets"),
        ("training", "Train model"),
        ("evaluations", "Evaluations"),
        ("inference", "Inference"),
        ("optimiser", "Optimiser"),
    ]:
        console.goto(f"/{route}?projectId={project}")
        console.sees(text)
        console.healthy()
        console.save_evidence(f"project-surface-{route}")


def test_decision_deployment_without_training_data_never_invents_an_example(console, workshop):
    project = workshop.project()["id"]
    deployed = DeployedModel.objects.create(
        project_id=project,
        model_id="ft-titanic-decision",
        base_model_id="Qwen/Qwen3.5-0.8B",
        status="ready",
        inference_contract="decision",
        max_model_len=4096,
        gpu_type="H100",
        quantization="bf16",
    )
    console.goto(f"/inference/{deployed.pk}?projectId={project}")
    console.sees(deployed.model_id)
    console.press("View API snippet")
    console.sees("TypeSafe systemone. Point the SDK base URL at this API.")
    console.sees(
        "A complete example input is unavailable for this model’s pinned training version."
    )
    console.wait_until(
        "() => !document.querySelector('[role=dialog]').innerText.includes('/chat/completions')"
    )
    console.wait_until(
        "() => !document.querySelector('[role=dialog]').innerText.includes('Use live alias')"
    )
    console.wait_until(
        "() => getComputedStyle(document.querySelector('[role=dialog]')).opacity === '1'"
    )
    console.healthy()
    console.save_evidence("decision-inference-snippet")


def test_data_onboarding_uploads_once_and_hands_off_training_preparation(console, tmp_path):
    console.goto("/projects")
    console.press("New project")
    console._browser.call(
        lambda: console._page.get_by_label("Project name", exact=True).fill(
            "Training onboarding fixture"
        )
    )
    console.upload('input[type="file"]', tickets(tmp_path))
    console.sees("3 rows")
    console.wait_until(
        "() => document.querySelector('[role=dialog]').getAnimations().every(a => a.playState !== 'running')"
    )
    console.save_evidence("data-onboarding-upload")
    console.press("Create dataset")
    console.sees("Prepare your training data")
    dataset = Dataset.objects.get(name="Training onboarding fixture")
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
    console._browser.call(lambda: console._page.set_viewport_size({"width": 390, "height": 844}))
    console.wait_until(
        "() => document.querySelector('[role=dialog]').getAnimations().every(a => a.playState !== 'running')"
    )
    bounds = console._browser.call(
        lambda: console._page.evaluate("""() => {
        const d = document.querySelector('[role=dialog]'); const r = d.getBoundingClientRect();
        return {left:r.left, right:r.right, viewport:innerWidth, width:getComputedStyle(d).width, maxWidth:getComputedStyle(d).maxWidth, translate:getComputedStyle(d).translate, transform:getComputedStyle(d).transform};
    }""")
    )
    assert bounds["left"] >= 0 and bounds["right"] <= bounds["viewport"], bounds
    console.save_evidence("data-onboarding-handoff-mobile")
    console._browser.call(
        lambda: (
            console._page.get_by_role("dialog")
            .get_by_role("button", name="Close", exact=True)
            .last.click()
        )
    )
    console.wait_until("() => !location.search.includes('prepare=')")
    console.sees("Training onboarding fixture")
    console.healthy()
