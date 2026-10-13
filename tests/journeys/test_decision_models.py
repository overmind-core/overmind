import json

from fakes.modal import DecisionBackend

from .datasets import until, upload
from .stack import drain

RECONCILE = "overbae.tasks.decision_jobs.reconcile"
ROUTE = {
    "type": "choice",
    "instructions": "Which team handles this ticket?",
    "criteria": {"billing": "Billing", "shipping": "Shipping"},
}


def _rows(path, tickets):
    path.write_text(
        "".join(
            json.dumps(
                {
                    "input": {"state": f"Ticket: {text}", "questions": {"route": ROUTE}},
                    "expected_output": {"route": team},
                }
            )
            + "\n"
            for text, team in tickets
        )
    )
    return path


def test_a_dataset_of_decision_rows_trains_a_model_that_answers(
    cli, sample_agent, mcp_for, rest_for, worker, fake_modal, beat, tmp_path
):
    decision = DecisionBackend(fake_modal).install()
    cli.scan(sample_agent)
    cli.sync(sample_agent)
    key = cli.project_key(sample_agent)
    mcp, rest = mcp_for(key), rest_for(key)
    train = upload(
        cli,
        sample_agent,
        _rows(
            tmp_path / "train.jsonl",
            [("I was charged twice.", "billing"), ("My parcel is lost.", "shipping")] * 3,
        ),
        "--intent",
        "train",
    )
    evaluation = upload(
        cli,
        sample_agent,
        _rows(
            tmp_path / "eval.jsonl", [("Refund order 7.", "billing"), ("Late parcel.", "shipping")]
        ),
        "--intent",
        "eval",
    )
    drain(worker)
    fits = mcp.call("inspect_dataset", {"dataset": train})["active"]["fits"]
    assert fits == {"ok": True, "reason": "", "format": "decision"}

    job = mcp.call(
        "start_decision_job",
        {"model": "Cloudflare/clef-flash", "dataset": train, "eval_dataset": evaluation},
    )["job"]
    drain(worker)

    def status():
        return mcp.call("get_job", {"kind": "decision_job", "id": job["id"]})["status"]

    until(beat, lambda: status() == "running", RECONCILE)
    assert len(decision.staged[job["id"]]["records"].splitlines()) == 6
    fake_modal.release("train_decision")
    until(beat, lambda: status() == "evaluating", RECONCILE)
    fake_modal.release("evaluate_decision")
    until(beat, lambda: status() == "succeeded", RECONCILE)

    finished = rest.request("GET", f"/api/decision-jobs/{job['id']}/")
    assert finished["evaluation"]["trained"]["accuracy"] == 0.5
    answered = rest.request(
        "POST",
        f"/api/decision-jobs/{job['id']}/systemone/",
        json={"state": "Ticket: where is my refund?", "questions": {"route": ROUTE}},
    )
    assert answered["answers"]["route"]["probabilities"] == {"billing": 0.9, "shipping": 0.1}
    assert decision.scored[-1]["adapter"] == f"/artifacts/{job['id']}/adapter"
