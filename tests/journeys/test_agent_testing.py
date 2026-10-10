from .datasets import JUDGE, evaluate, tickets, upload
from .stack import drain


def test_an_evaluation_grades_on_card_criteria_with_a_frozen_judge(
    workshop, cli, sample_agent, worker, tmp_path, fake_llm
):
    answer = workshop.capability("answer")
    dataset = upload(
        cli, sample_agent, tickets(tmp_path), "--intent", "eval", "--capability", answer["id"]
    )
    drain(worker)
    fake_llm.fail(
        lambda r: r.schema_name == "ChecklistResult" and "Refund order 1 please" in r.text,
        status=400,
    )

    run = evaluate(workshop, dataset)
    drain(worker)
    result = workshop.read(run["resource"]["uri"])

    assert result["status"] == "completed"
    judged = [e for e in result["run_evaluators"] if e["kind"] == "llm_judge"]
    assert judged and all(e["judge_model"] == JUDGE for e in judged)

    [variant] = result["summary"]["items"].values()
    criteria = {item["id"] for item in variant["Card Criteria Compliance"]}
    assert criteria == {
        "the-reply-leaves-out-the-order-id",
        "the-reply-quotes-the-order-id-and-status-when-an",
    }

    errors = result["summary"]["error_counts"]
    assert errors["evaluator_errors"] == 1
    [metrics] = [v["metrics"] for v in result["summary"]["variants"].values()]
    compliance = metrics["Card Criteria Compliance"]
    assert compliance["mean"] == 1.0
