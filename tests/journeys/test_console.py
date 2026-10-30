import hashlib
import json

from django.utils import timezone

from overbae.models import (
    Cell,
    Dataset,
    DatasetContext,
    DatasetPipeline,
    DatasetPipelineRun,
    DedicatedDeployment,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    NativeEvaluationPlan,
    OperationalRun,
)
from overbae.services import activity as activity_service

from .datasets import tickets, upload
from .stack import drain, settle


def test_dataset_inventory_follows_upload_bytes_and_publication(
    console, workshop, rest_for, worker
):
    project = workshop.project()["id"]
    dataset = workshop.call(
        "start_dataset", {"name": "Live upload", "brief": "Inspect uploaded rows"}
    )["dataset"]["id"]
    rest = rest_for(workshop.api_key)
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
    console.save_evidence("dataset-upload-progress")
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
    console.save_evidence("dataset-upload-ready")


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
    console.upload('[role="dialog"] input[type="file"]', tickets(tmp_path))
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


def test_activity_connects_pipeline_sequence(console, workshop):
    project = workshop.project()["id"]
    dataset = Dataset.objects.create(project_id=project, name="Connected work")
    source = Cell.objects.create(dataset=dataset, position=0, state="ok", fingerprint="a" * 64)
    pipeline = DatasetPipeline.objects.create(
        project_id=project, name="Features", request_key="features"
    )
    for key, kind, action in (
        ("upload", "source_uploaded", "Uploaded source"),
        ("import", "source_ready", "Source ready"),
        ("save", "asset_changed", "Saved a transformation for"),
    ):
        activity_service.record(
            key=key,
            project_id=project,
            kind=kind,
            action=action,
            assets=[activity_service.asset("dataset", dataset.pk, dataset.name)],
        )
    activity_service.record(
        key="unrelated-edit",
        project_id=project,
        kind="asset_changed",
        action="Updated dataset",
        assets=[activity_service.asset("dataset", dataset.pk, dataset.name)],
    )
    for index in range(2):
        run = DatasetPipelineRun.objects.create(
            dataset=dataset,
            source=source,
            pipeline=pipeline,
            request_key=f"sequence-{index}",
            state="completed",
        )
        activity_service.record(
            key=f"sequence-{index}",
            project_id=project,
            kind="task_requested",
            action="Requested a transformation of",
            actor_kind="codex",
            actor_name="Codex",
            task_kind="dataset_pipeline",
            task_id=run.pk,
            assets=[activity_service.asset("dataset", dataset.pk, dataset.name)],
        )
    records = workshop.read("overmind://activity")["results"]
    linked = [item for item in records if item["sequence_key"]]
    assert len(linked) == 5
    assert len({item["sequence_key"] for item in linked}) == 1
    assert linked[0]["sequence_key"]
    assert all(not item["sequence_key"] for item in records if item["action"] == "Updated dataset")
    console.goto(f"/datasets?projectId={project}")
    console.press("unread notification")
    console.wait_until("() => document.querySelectorAll('[data-sequence-key]').length === 5")
    console.save_evidence("connected-pipeline-events")


def test_dataset_preparation_persists_between_jobs_until_agent_finishes(
    console, workshop, cli, sample_agent, worker, tmp_path
):
    project = workshop.project()["id"]
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
    console.sees("Preparing")
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
    current = workshop.call("inspect_dataset_workbench", {"dataset": dataset})[
        "preparation_workflow"
    ]
    assert current["state"] == "preparing"
    events = workshop.read("overmind://activity")["results"]
    preparation_events = [e for e in events if e["kind"] == "dataset_preparation"]
    assert len(preparation_events) == 1
    event_id = preparation_events[0]["id"]
    assert not [e for e in events if e["task_kind"] == "dataset_pipeline"]
    workshop.call("update_dataset_preparation", {**workflow, "state": "paused"})
    console.sees("Preparation paused")
    workshop.call("update_dataset_preparation", {**workflow, "state": "preparing"})
    console.sees("Preparing")
    console.press("unread notification")
    console.wait_until(
        f"() => document.querySelector('[data-activity-id=\"{event_id}\"]')?.dataset.activityStatus === 'running'"
    )
    console.wait_until(
        "() => document.getAnimations().every(a => a.effect?.getTiming().iterations === Infinity || a.playState !== 'running')"
    )
    console.save_evidence("dataset-preparation-active")
    output = workshop.call("inspect_dataset", {"dataset": dataset})["active"]["id"]
    completion = {**workflow, "state": "completed", "outputs": [output]}
    workshop.call("update_dataset_preparation", completion)
    workshop.call("update_dataset_preparation", completion)
    console.wait_until(
        f"() => document.querySelector('[data-activity-id=\"{event_id}\"]')?.dataset.activityStatus === 'completed'"
    )
    console.sees("Ready")
    console.save_evidence("dataset-preparation-completed")
    assert workshop.call_result(
        "update_dataset_preparation", {**workflow, "state": "preparing"}
    ).isError
    console.healthy()


def test_dedicated_startup_updates_and_keeps_configuration_after_stop(console, workshop):
    project = workshop.project()["id"]
    deployed = DeployedModel.objects.create(
        project_id=project,
        model_id="ft-dedicated-startup",
        status="ready",
        base_model_id="meta-llama/Llama-3.2-3B-Instruct",
        gpu_type="H100",
    )
    row = DedicatedDeployment.objects.create(
        project_id=project,
        deployed_model=deployed,
        gpu_type="H100",
        status="warming",
        min_replicas=2,
        max_replicas=3,
        rate_usd_per_gpu_hour=4,
        credits_per_gpu_hour=400,
        probe_call_id="fc-console-startup",
    )
    run = OperationalRun.objects.create(
        project_id=project,
        kind="dedicated_deployment",
        reference=str(row.pk),
        attempt=row.probe_call_id,
        snapshot={"stage": "loading_weights"},
        provider_state={
            "available": True,
            "current": {"message": "Loading model weights"},
            "logs": [
                {
                    "stage": "loading_weights",
                    "message": "Loading model weights",
                    "at": timezone.now().isoformat(),
                    "worker": "ta-console",
                    "completed": 1,
                    "total": 2,
                    "unit": "tensors",
                }
            ],
        },
    )
    console.goto(f"/inference/{deployed.pk}?projectId={project}")
    console.sees("Loading model weights")
    console.wait_until("() => !document.body.innerText.includes('Startup logs')")
    console.wait_until("""() => {
        const bar = document.querySelector('[role=progressbar][aria-label="Startup stages"]');
        const title = document.querySelector('[aria-label="Dedicated endpoint"] h2');
        const controls = document.querySelector('[aria-label="GPU controls"]');
        if (!bar || bar.children.length < 2) return false;
        const b = bar.getBoundingClientRect(), t = title.getBoundingClientRect(), c = controls.getBoundingClientRect();
        return b.left > t.right && b.right < c.left && Math.abs(b.y - t.y) < 12;
    }""")
    console.save_evidence("dedicated-startup-header")
    run.snapshot = {"stage": "capturing_graphs"}
    run.provider_state["current"] = {"message": "Capturing CUDA graphs"}
    run.save()
    console.sees("Capturing CUDA graphs")
    DedicatedDeployment.objects.filter(pk=row.pk).update(status="online", running_replicas=2)
    console.sees("Online")
    console.wait_until(
        """() => !document.querySelector('[role=progressbar][aria-label="Startup stages"]') && !document.body.innerText.includes('Startup logs')"""
    )
    console.save_evidence("dedicated-online")
    DedicatedDeployment.objects.filter(pk=row.pk).update(status="offline", running_replicas=0)
    console.sees("Offline")
    console.sees("0 running · 2–3 configured")
    console.press("Dedicated API snippet")
    console.sees("Call this model")
    console.wait_until(
        "() => document.querySelector('[role=dialog]').innerText.includes('dedicated/ft-dedicated-startup')"
    )
    console.save_evidence("dedicated-offline-snippet")
    console.press("Close")
    console.press("Configure GPU")
    console.sees("Dedicated GPU")
    console.wait_until("""() => {
        const dialog = document.querySelector('[role=dialog]');
        return dialog && dialog.querySelector('#dedicated-min-replicas')?.value === '2';
    }""")
    console.save_evidence("dedicated-configuration-dialog")
    console.healthy()
