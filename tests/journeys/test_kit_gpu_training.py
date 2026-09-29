import pytest

from .datasets import tickets, training_rows, until, upload
from .stack import drain

PREPARE = "overbae.tasks.training_preparation.reconcile"
TRAIN = "overbae.tasks.finetuning_reconciler.reconcile_finetuning_jobs"


@pytest.fixture
def training(workshop, cli, sample_agent, worker, sft, fake_modal, beat, tmp_path):
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
    job = workshop.call(
        "start_finetune",
        {
            "dataset": train,
            "eval_dataset": evals,
            "base_model": "Qwen/Qwen3-1.7B",
            "capability": "answer",
            "eval_model_before": False,
        },
    )["finetune"]["id"]
    drain(worker)
    until(beat, lambda: fake_modal.pending("prepare_"), PREPARE, TRAIN)
    fake_modal.release("prepare_")
    until(beat, lambda: fake_modal.pending("sft_"), PREPARE, TRAIN)
    return job


def status(workshop, job) -> dict:
    return workshop.read(f"overmind://finetunes/{job}")


def test_a_gpu_failure_fails_the_job_and_deploys_nothing(training, workshop, sft, fake_modal, beat):
    sft.crash("CUDA out of memory")
    until(beat, lambda: status(workshop, training)["status"] == "failed", TRAIN)
    state = status(workshop, training)
    assert state["error"]
    assert state["deployed_model"] is None
    assert not fake_modal.called("publish_adapter")


def test_a_cancelled_job_stops_the_gpu_call(
    training, workshop, rest_for, cli, sample_agent, fake_modal, beat
):
    rest_for(cli.project_key(sample_agent)).request(
        "POST", f"/api/finetuning-jobs/{training}/cancel/"
    )
    until(beat, lambda: status(workshop, training)["status"] == "cancelled", TRAIN)
    assert [c.state for c in fake_modal.calls.values() if c.name.startswith("sft_")] == [
        "cancelled"
    ]
    assert fake_modal.called("mark_cancelled")
