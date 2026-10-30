import json
import re

import requests

from .datasets import until, upload
from .stack import drain

PREPARE = "overbae.tasks.training_preparation.reconcile"
TRAIN = "overbae.tasks.finetuning_reconciler.reconcile_finetuning_jobs"
DEPLOY = "overbae.tasks.inference_controller.reconcile_deployments"
EVALUATE = "overbae.tasks.native_evaluation.reconcile"
MODEL = "OvermindLab/qwen-3.5-0.8B-decision"
FOUNDATION = "Qwen/Qwen3.5-0.8B"
QUESTION = "Approve refund?"


def _state(i: int) -> str:
    return f"Refund request {i}: order {i} arrived damaged."


def _approves(state: str) -> bool:
    return int(re.search(r"Refund request (\d+)", state)[1]) % 3 != 0


def _rows(path, n, *, evaluation: bool):
    rows = []
    for i in range(n):
        target = [0, 1] if _approves(_state(i)) else [1, 0]
        decision = {
            "state": _state(i),
            "question": QUESTION,
            "kind": "choice",
            "options": ["No", "Yes"],
        }
        if evaluation:
            rows.append(
                {"input": {"decision": decision}, "expected_output": {"probabilities": target}}
            )
        else:
            rows.append(
                {
                    "decision": {
                        **decision,
                        "target_probabilities": target,
                        "target_semantics": "categorical_gold",
                    }
                }
            )
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def _answer(request: dict, trained: bool) -> list[float]:
    if not trained:
        return [0.5, 0.5]
    return [0.1, 0.9] if _approves(request["state"]) else [0.9, 0.1]


def test_decide(
    workshop,
    cli,
    sample_agent,
    rest_for,
    live_api,
    worker,
    sft,
    serving,
    decisions,
    fake_modal,
    beat,
    console,
    tmp_path,
):
    decisions.answer = _answer
    project = workshop.project()["id"]
    rest = rest_for(workshop.api_key)
    train = upload(
        cli,
        sample_agent,
        _rows(tmp_path / "train.jsonl", 40, evaluation=False),
        "--intent",
        "train",
    )
    evals = upload(
        cli, sample_agent, _rows(tmp_path / "eval.jsonl", 12, evaluation=True), "--intent", "eval"
    )
    drain(worker)
    readiness = workshop.call("check_finetune_readiness", {"dataset": train})
    assert readiness["dataset"]["validation"]["valid"] is True
    assert readiness["dataset"]["validation"]["format"] == "decision"
    assert readiness["training_contract"]["inference_contract"] == "decision"

    started = workshop.call(
        "start_decision_job",
        {
            "request_key": "approve-refunds",
            "dataset": train,
            "eval_dataset": evals,
            "base_model": MODEL,
        },
    )
    job = started["training_run"]["id"]
    drain(worker)

    def finetune():
        return workshop.read(f"overmind://finetunes/{job}")

    until(beat, lambda: fake_modal.pending("prepare_"), PREPARE, TRAIN)
    fake_modal.release("prepare_")
    until(beat, lambda: fake_modal.pending("sft_"), PREPARE, TRAIN)
    fake_modal.release("sft_")
    until(beat, lambda: finetune()["deployed_model"], PREPARE, TRAIN, DEPLOY)
    deployment = finetune()["deployed_model"]
    until(
        beat,
        lambda: workshop.read(f"overmind://deployments/{deployment}")["status"] == "ready",
        TRAIN,
        DEPLOY,
    )
    until(beat, lambda: finetune()["status"] == "succeeded", TRAIN, DEPLOY)
    record = finetune()["record"]
    assert record["contract"]["inference_contract"] == "decision"
    assert record["artifact"]["reload_verification"]["decisions"] > 0
    labels = record["prediction_labels"]
    assert labels["state"] == "available"
    assert [s["options"] for s in labels["label_sets"]] == [["No", "Yes"]]

    final_cell = workshop.call("inspect_dataset", {"dataset": evals})["active"]["id"]
    comparison = workshop.call(
        "create_native_evaluation",
        {
            "name": "Refund approvals before and after training",
            "request_key": "approve-refunds-quality",
            "final_cell": final_cell,
            "baseline": "base",
            "bootstrap_samples": 2,
            "participants": [
                {"key": "base", "name": "Base model", "kind": "foundation", "model": FOUNDATION},
                {"key": "trained", "name": "Trained model", "kind": "trained", "job": job},
            ],
        },
    )
    evaluation = comparison["workflow"]["id"]
    workshop.call("launch_native_evaluation", {"evaluation": evaluation})

    def measured():
        return workshop.call("inspect_native_evaluation", {"evaluation": evaluation})

    until(beat, lambda: measured()["monitoring"]["evaluation_state"] == "completed", EVALUATE)
    arms = {arm["key"]: arm["benchmarks"][0] for arm in measured()["monitoring"]["arms"]}
    assert arms["base"]["scored"] == arms["trained"]["scored"] == 12
    before, after = arms["base"]["raw"], arms["trained"]["raw"]
    assert after["cross_entropy"]["mean"] < before["cross_entropy"]["mean"]
    assert after["accuracy"]["mean"] > before["accuracy"]["mean"]
    monitor = f"/api/native-evaluations/{evaluation}/monitor/"
    assert rest.request("GET", monitor)["count"] == 12
    assert rest.request("GET", monitor + "?outcome=incorrect&participant=base")["count"] == 8
    assert rest.request("GET", monitor + "?outcome=incorrect&participant=trained")["count"] == 0

    group = rest.request("GET", f"/api/finetuning-jobs/{job}/")["group_id"]
    console.goto(f"/training?projectId={project}&groupId={group}&jobId={job}")
    console.sees("Prediction labels")
    console.sees("2 labels")
    console.sees("Succeeded")
    console.goto(f"/evaluations/runs/{evaluation}?projectId={project}&format=decision")
    console.sees("Model comparison")
    console.sees("Trained model")
    console.sees("100.0%")
    console.save_evidence("decide-quality")

    model_id = workshop.read(f"overmind://deployments/{deployment}")["model_id"]
    answered = requests.post(
        f"{live_api.url}/api/v1/systemone",
        headers={"Authorization": f"Bearer {workshop.api_key}"},
        json={
            "model": model_id,
            "state": _state(4),
            "questions": {
                "approve": {
                    "type": "choice",
                    "instructions": QUESTION,
                    "criteria": {"No": "", "Yes": ""},
                }
            },
        },
        timeout=30,
    )
    assert answered.status_code == 200, answered.text
    approve = answered.json()["answers"]["approve"]
    assert approve["choice"] == "Yes"
    assert approve["probabilities"] == {"No": 0.1, "Yes": 0.9}
