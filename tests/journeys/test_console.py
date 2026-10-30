import json

from django.utils import timezone

from overbae.models import (
    Cell,
    Dataset,
    DatasetContext,
    EvalRun,
    FinetuningJob,
    NativeEvaluationPlan,
)

from .datasets import upload
from .stack import drain


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
