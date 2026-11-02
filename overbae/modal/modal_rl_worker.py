"""Single-turn GRPO on Modal, rewarded by the project's deterministic check scripts.

One call runs the whole loop on one GPU: score the base model on the held-out prompts,
train a LoRA adapter with TRL's GRPOTrainer (each step samples a group of completions per
prompt and scores every completion with the checks), then score the trained adapter on
the same held-out prompts. Events go to the ``overmind-rl-events`` Queue under the run's
partition; ``overbae.services.rl_training.poll`` drains it.

Deploy:
  MODAL_ENVIRONMENT=overmind-dev modal deploy overbae/modal/modal_rl_worker.py --env overmind-dev
"""

from __future__ import annotations

import json
import time
import traceback
from typing import Any

import modal

APP_NAME = "overmind-rl"
EVENTS_QUEUE = "overmind-rl-events"
CACHE_MOUNT = "/cache"
GPU = "H100"
TIMEOUT_S = 3 * 60 * 60
TEXT_LIMIT = 1_500

image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("torch==2.10.0", index_url="https://download.pytorch.org/whl/cu128")
    .pip_install(
        "transformers==5.17.0",
        "trl==1.13.0",
        "peft>=0.17.0",
        "accelerate>=1.4.0",
        "datasets==4.7.0",
        "sentencepiece",
        "protobuf",
    )
    .env(
        {
            "HF_HOME": f"{CACHE_MOUNT}/hf",
            "HF_XET_HIGH_PERFORMANCE": "1",
            "PYTHONUNBUFFERED": "1",
            "TOKENIZERS_PARALLELISM": "false",
        }
    )
    .add_local_python_source("modal_shared")
)

app = modal.App(APP_NAME)
cache = modal.Volume.from_name("overmind-rl-cache", create_if_missing=True)
events = modal.Queue.from_name(EVENTS_QUEUE, create_if_missing=True)


def clip(text: str) -> str:
    text = text or ""
    return text if len(text) <= TEXT_LIMIT else text[:TEXT_LIMIT] + " …"


def parse_json(value: Any) -> Any:
    if isinstance(value, str) and value.strip()[:1] in ("[", "{"):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def messages_of(row: dict[str, Any], prompt_column: str, system_prompt: str) -> list[dict]:
    value = parse_json(row.get(prompt_column))
    if isinstance(value, dict) and isinstance(value.get("messages"), list):
        value = value["messages"]
    if isinstance(value, list) and all(isinstance(m, dict) and "role" in m for m in value):
        messages = [{"role": m["role"], "content": m.get("content") or ""} for m in value]
        last_user = max((i for i, m in enumerate(messages) if m["role"] == "user"), default=-1)
        messages = messages[: last_user + 1] if last_user >= 0 else messages
    else:
        messages = [{"role": "user", "content": "" if value is None else str(value)}]
    if system_prompt and not any(m["role"] == "system" for m in messages):
        messages.insert(0, {"role": "system", "content": system_prompt})
    return messages


def render(tokenizer, row: dict[str, Any], job: dict[str, Any]) -> str:
    tools = parse_json(row.get(job["tools_column"])) if job.get("tools_column") else None
    return tokenizer.apply_chat_template(
        messages_of(row, job["prompt_column"], job.get("system_prompt") or ""),
        tools=tools if isinstance(tools, list) and tools else None,
        add_generation_prompt=True,
        tokenize=False,
        enable_thinking=False,
    )


def summarize(results: list[dict[str, Any]], keys: list[str]) -> dict[str, Any]:
    count = len(results) or 1
    per_check = {}
    for key in keys:
        outcomes = [r["checks"][key] for r in results]
        scored = [
            o["score"] for o in outcomes if o["outcome"] == "scored" and o["score"] is not None
        ]
        per_check[key] = {
            "mean": sum(scored) / len(scored) if scored else None,
            "scored": len(scored),
            "abstained": sum(1 for o in outcomes if o["outcome"] == "abstained"),
            "errors": sum(1 for o in outcomes if o["outcome"] == "error"),
        }
    return {
        "pass_rate": sum(1 for r in results if r["passed"]) / count,
        "mean_reward": sum(r["reward"] for r in results) / count,
        "count": len(results),
        "per_check": per_check,
    }


@app.function(image=image, gpu=GPU, timeout=TIMEOUT_S, volumes={CACHE_MOUNT: cache})
def train(job: dict[str, Any]) -> dict[str, Any]:
    started = time.monotonic()
    run_id = job["run_id"]

    def emit(kind: str, **body: Any) -> None:
        events.put({"type": kind, "at": time.time(), **body}, partition=run_id)

    try:
        return _train(job, emit, started)
    except Exception as exc:
        emit(
            "error",
            message=f"{type(exc).__name__}: {exc}"[:2000],
            trace=traceback.format_exc()[-4000:],
            gpu_seconds=time.monotonic() - started,
        )
        raise


def _train(job: dict[str, Any], emit, started: float) -> dict[str, Any]:
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoModelForCausalLM, AutoTokenizer, TrainerCallback
    from trl import GRPOConfig, GRPOTrainer

    from modal_shared.check_rewards import CheckRunner, score_completion

    hp = job["hyperparameters"]
    rewards = job["rewards"]
    keys = [reward["key"] for reward in rewards]
    runner = CheckRunner(job["runner_source"])
    group_size = int(hp["group_size"])

    emit("status", status="loading_model", gpu=GPU)
    tokenizer = AutoTokenizer.from_pretrained(job["base_model"])
    tokenizer.padding_side = "left"
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token
    model = AutoModelForCausalLM.from_pretrained(
        job["base_model"], dtype=torch.bfloat16, device_map="cuda"
    )
    cache.commit()

    train_rows, holdout_rows = job["train_rows"], job["holdout_rows"]
    train_prompts = [render(tokenizer, row, job) for row in train_rows]
    holdout_prompts = [render(tokenizer, row, job) for row in holdout_rows]

    @torch.no_grad()
    def generate(current, prompts: list[str]) -> list[str]:
        current.eval()
        outputs: list[str] = []
        for start in range(0, len(prompts), 16):
            batch = tokenizer(
                prompts[start : start + 16],
                return_tensors="pt",
                padding=True,
                add_special_tokens=False,
            ).to("cuda")
            generated = current.generate(
                **batch,
                max_new_tokens=int(hp["max_completion_tokens"]),
                do_sample=False,
                pad_token_id=tokenizer.pad_token_id,
            )
            outputs += tokenizer.batch_decode(
                generated[:, batch["input_ids"].shape[1] :], skip_special_tokens=True
            )
        return outputs

    def evaluate(current, stage: str) -> dict[str, Any]:
        emit("status", status=stage)
        outputs = generate(current, holdout_prompts)
        results = [
            score_completion(runner, rewards, row, prompt, output)
            for row, prompt, output in zip(holdout_rows, holdout_prompts, outputs, strict=True)
        ]
        samples = [
            {
                "index": index,
                "prompt": clip(row.get("_prompt_text") or ""),
                "output": clip(output),
                "reward": result["reward"],
                "passed": result["passed"],
                "checks": result["checks"],
            }
            for index, (row, output, result) in enumerate(
                zip(holdout_rows, outputs, results, strict=True)
            )
        ]
        summary = summarize(results, keys)
        emit(stage, metrics=summary, samples=samples)
        return summary

    baseline = evaluate(model, "baseline")

    def checks(prompts, completions, row_index, trainer_state=None, log_metric=None, **_):
        results = [
            score_completion(runner, rewards, train_rows[i], prompt, completion)
            for prompt, completion, i in zip(prompts, completions, row_index, strict=True)
        ]
        batch = summarize(results, keys)
        if log_metric is not None:
            log_metric("pass_rate", batch["pass_rate"])
            for key, stats in batch["per_check"].items():
                if stats["mean"] is not None:
                    log_metric(f"checks/{key}", stats["mean"])
        step = (trainer_state.global_step if trainer_state is not None else 0) + 1
        first = row_index[0]
        emit(
            "group",
            step=step,
            prompt=clip(train_rows[first].get("_prompt_text") or ""),
            attempts=[
                {
                    "text": clip(completion),
                    "reward": result["reward"],
                    "passed": result["passed"],
                    "checks": result["checks"],
                }
                for completion, result, i in zip(completions, results, row_index, strict=True)
                if i == first
            ][:group_size],
            batch=batch,
        )
        return [result["reward"] for result in results]

    class Progress(TrainerCallback):
        def on_log(self, args, state, control, logs=None, **kwargs):
            if logs and "reward" in logs:
                emit(
                    "step",
                    step=state.global_step,
                    max_steps=state.max_steps,
                    metrics={k: v for k, v in logs.items() if isinstance(v, (int, float))},
                )

    generation_batch = group_size * int(hp["prompts_per_step"])
    micro_batch = next(size for size in (4, 2, 1) if generation_batch % size == 0)
    config = GRPOConfig(
        output_dir="/tmp/grpo",
        num_generations=group_size,
        # One generation batch per optimizer step, trained in micro-batches: full-vocab
        # logits for 16 long tool-calling prompts at once exceed an H100.
        per_device_train_batch_size=micro_batch,
        gradient_accumulation_steps=generation_batch // micro_batch,
        max_completion_length=int(hp["max_completion_tokens"]),
        temperature=float(hp["temperature"]),
        learning_rate=float(hp["learning_rate"]),
        beta=float(hp["beta"]),
        max_steps=int(hp["max_steps"]),
        logging_steps=1,
        save_strategy="no",
        report_to="none",
        bf16=True,
        # Tool-calling prompts run to thousands of tokens per completion.
        gradient_checkpointing=True,
        remove_unused_columns=False,
        seed=int(hp["seed"]),
    )
    trainer = GRPOTrainer(
        model=model,
        reward_funcs=[checks],
        args=config,
        train_dataset=Dataset.from_list(
            [{"prompt": prompt, "row_index": i} for i, prompt in enumerate(train_prompts)]
        ),
        processing_class=tokenizer,
        peft_config=LoraConfig(
            r=int(hp["lora_rank"]),
            lora_alpha=2 * int(hp["lora_rank"]),
            lora_dropout=0.0,
            target_modules="all-linear",
            task_type="CAUSAL_LM",
        ),
        callbacks=[Progress()],
    )
    emit("status", status="training", max_steps=int(hp["max_steps"]))
    trainer.train()

    final = evaluate(trainer.model, "final")
    adapter = f"{CACHE_MOUNT}/adapters/{job['run_id']}"
    trainer.model.save_pretrained(adapter)
    tokenizer.save_pretrained(adapter)
    cache.commit()
    runner.close()

    gpu_seconds = time.monotonic() - started
    emit("done", gpu_seconds=gpu_seconds, adapter=adapter)
    return {"baseline": baseline, "final": final, "gpu_seconds": gpu_seconds, "adapter": adapter}
