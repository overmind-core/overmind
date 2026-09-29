import json
import re

from .conftest import GOOD_REPLY, answer_turn
from .datasets import tickets, upload
from .stack import drain

PREPARE = "overbae.tasks.training_preparation.reconcile"
TRAIN = "overbae.tasks.finetuning_reconciler.reconcile_finetuning_jobs"
DEPLOY = "overbae.tasks.inference_controller.reconcile_deployments"
BAD_REPLY = "Your refund is on its way. The order was delivered."


def training_rows(tmp_path, n=12):
    path = tmp_path / "train.jsonl"
    path.write_text(
        "".join(
            json.dumps(
                {
                    "messages": [
                        {"role": "system", "content": "You are a support agent."},
                        {"role": "user", "content": f"Refund order {i}"},
                        {"role": "assistant", "content": f"Order {i} was delivered."},
                    ]
                }
            )
            + "\n"
            for i in range(n)
        )
    )
    return path


def until(beat, condition, *tasks, limit=30):
    for _ in range(limit):
        beat(*tasks)
        if condition():
            return
    raise AssertionError(f"{condition.__name__} not met after {limit} ticks")


def trained_turn(request):
    reply = answer_turn(request)
    if reply.get("tool_calls"):
        return reply
    return {"content": GOOD_REPLY}


def test_a_trained_model_is_benchmarked_served_and_switched_in(
    workshop,
    cli,
    sample_agent,
    worker,
    sft,
    serving,
    fake_modal,
    beat,
    fake_llm,
    live_api,
    tmp_path,
):
    def served(prefix):
        return lambda r: "inference.test" in r.url and r.model.startswith(prefix)

    fake_llm.on(served("base--"), BAD_REPLY)
    fake_llm.on(served("ft-"), trained_turn)
    fake_llm.on("You triage customer support tickets.", "refund")
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
        },
    )["finetune"]["id"]
    drain(worker)

    def finetune():
        return workshop.read(f"overmind://finetunes/{job}")

    until(beat, lambda: fake_modal.pending("prepare_"), PREPARE, TRAIN)
    beat(PREPARE, TRAIN)
    assert not fake_modal.called("sft_")

    fake_modal.release("prepare_")
    until(beat, lambda: fake_modal.called("sft_"), PREPARE, TRAIN)
    order = [name for _, name, _, _ in fake_modal.log]
    assert order.index("upload_dataset") < order.index(
        next(n for n in order if n.startswith("sft_"))
    )
    pinned = {c["id"]: c for c in workshop.call("inspect_dataset", {"dataset": train})["cells"]}
    assert pinned[finetune()["cell"]]["frozen"] is True

    fake_modal.release("sft_")

    def benchmarked():
        state = finetune()
        evals = {e["kind"]: e for e in state["progress"].get("judge_evals") or []}
        return state["status"] == "succeeded" and all(
            evals.get(kind, {}).get("status") == "completed" for kind in ("model_before", "final")
        )

    until(beat, benchmarked, PREPARE, TRAIN, DEPLOY)
    state = finetune()
    assert [p["train_loss"] for p in state["loss"]] == [1.0, 0.5]
    evals = {e["kind"]: e for e in state["progress"]["judge_evals"]}
    assert evals["final"]["aggregate_score"] > evals["model_before"]["aggregate_score"]
    assert evals["final"]["baseline_delta"] > 0

    deployment = state["deployed_model"]
    workshop.call("set_active_model", {"capability": "answer", "deployment": deployment})
    until(
        beat,
        lambda: (workshop.capability("answer")["active_model"] or {}).get("id") == deployment,
        DEPLOY,
    )
    model_id = workshop.read(f"overmind://deployments/{deployment}")["model_id"]
    assert serving.warmed.count(model_id) >= 2

    prompt = workshop.call("get_model_swap_prompt", {"finetune": job})["prompt"]
    alias = re.search(
        r'Set the model parameter of the capability\'s LLM calls to "([^"]+)"', prompt
    )[1]
    base_url = re.search(r"Overmind inference base URL: (\S+)", prompt)[1]
    agent = sample_agent.repo / "support_desk" / "agent.py"
    source = agent.read_text()
    agent.write_text(
        source.replace(
            "model=MODEL, messages=messages, tools=[LOOKUP_ORDER]",
            f'model="{alias}", messages=messages, tools=[LOOKUP_ORDER]',
        )
    )

    replies = sample_agent.run(
        "Refund order 42 please",
        api_url=live_api.url,
        llm_url=base_url,
        llm_key=cli.project_key(sample_agent),
    )
    assert replies[-1] == GOOD_REPLY
    metrics = workshop.read(f"overmind://deployments/{deployment}")["metrics"]
    assert metrics["request_count"] >= 2
