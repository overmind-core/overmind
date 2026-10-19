from .datasets import JUDGE, tickets, upload
from .stack import drain

WEAK_REPLY = "Your refund is on its way."


def test_a_backtest_compares_every_candidate_with_the_recorded_baseline(
    workshop, cli, sample_agent, worker, tmp_path, fake_llm
):
    fake_llm.extra_models.append("fake/weak")
    fake_llm.on(lambda r: r.model == "fake/weak", WEAK_REPLY)
    answer = workshop.capability("answer")
    dataset = upload(
        cli, sample_agent, tickets(tmp_path), "--intent", "eval", "--capability", answer["id"]
    )
    drain(worker)

    group = workshop.call(
        "run_evaluation",
        {
            "name": "backtest",
            "dataset": dataset,
            "judge_model": JUDGE,
            "targets": [
                {"mode": "recorded", "label": "recorded", "is_baseline": True},
                {"mode": "generate", "model_name": "fake/candidate", "label": "candidate"},
                {"mode": "generate", "model_name": "fake/weak", "label": "weak"},
            ],
        },
    )
    drain(worker)

    runs = {run["label"]: workshop.read(run["resource"]["uri"]) for run in group["runs"]}
    assert {label: run["status"] for label, run in runs.items()} == dict.fromkeys(
        ("recorded", "candidate", "weak"), "completed"
    )
    assert len({tuple(m["name"] for m in run["matrix"]) for run in runs.values()}) == 1

    compared = {
        run["label"]: workshop.call("compare_evaluations", {"run": run["id"]})
        for run in group["runs"]
        if not run["is_baseline"]
    }
    assert all(c["baseline_label"] == "recorded" and c["paired"] == 3 for c in compared.values()), (
        compared
    )
    assert compared["candidate"]["overall"]["status"] == "unchanged"
    assert compared["weak"]["overall"]["status"] == "regressed"
    assert compared["weak"]["losses"] == 3
