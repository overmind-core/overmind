"""Offline tiny-model training qualification; no model downloads or provider calls."""

import argparse
import json
import os
import random
import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import torch
from peft import LoraConfig, get_peft_model
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from transformers import (
    GPT2Config,
    GPT2LMHeadModel,
    LlamaConfig,
    LlamaForCausalLM,
    PreTrainedTokenizerFast,
    Trainer,
    TrainingArguments,
)

from modal_shared.training_telemetry import read_telemetry, record_stage
from overbae.services.sft_assets.common import ProgressCallback
from overbae.services.sft_assets.training_monitor import (
    TrainingMonitorCallback,
    preserve_training_state,
)


def run(root, policy, *, structured=False):
    random.seed(42)
    np.random.seed(42)
    torch.manual_seed(42)
    vocabulary = {f"t{i}": i for i in range(32)}
    if structured:
        vocabulary.pop("t5")
        vocabulary['{"risk":"high"}'] = 5
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=Tokenizer(WordLevel(vocabulary, unk_token="t0")),
        pad_token="t0",
        eos_token="t1",
        unk_token="t2",
    )
    config = GPT2Config(
        vocab_size=32,
        n_positions=32,
        n_embd=16,
        n_layer=1,
        n_head=2,
        bos_token_id=2,
        eos_token_id=1,
        pad_token_id=0,
    )
    model = get_peft_model(
        GPT2LMHeadModel(config),
        LoraConfig(
            r=2,
            lora_alpha=4,
            lora_dropout=0.2,
            target_modules=["c_attn"],
            task_type="CAUSAL_LM",
        ),
    )
    model.train()
    model.gradient_checkpointing_enable()
    modes = [(module, module.training) for module in model.modules()]
    rng = torch.get_rng_state().clone()
    with preserve_training_state(model):
        torch.rand(4)
        np.random.random()
        random.random()
        # Unsloth's generation/evaluation mode disables recomputation independently of .training.
        model.gradient_checkpointing_disable()
    assert torch.equal(rng, torch.get_rng_state())
    assert all(module.training == training for module, training in modes)
    assert model.is_gradient_checkpointing
    rows = [
        {"key": str(i), "input_ids": [2, 3, 4, 5, 1], "labels": [-100, -100, -100, 5, 1]}
        for i in range(8)
    ]

    def dataset(tokenizer, rows, *, stage=None):
        if stage:
            record_stage(root, stage, completed=len(rows), total=len(rows), unit="rows")
        return [{"input_ids": row["input_ids"], "labels": row["labels"]} for row in rows]

    progress = ProgressCallback(root)
    callback = TrainingMonitorCallback(
        root,
        rows,
        rows,
        dataset,
        tokenizer,
        progress,
    )
    callback.policy = policy
    trainer = Trainer(
        model=model,
        train_dataset=dataset(tokenizer, rows),
        callbacks=[progress, callback],
        args=TrainingArguments(
            output_dir=str(root / "trainer"),
            max_steps=6,
            per_device_train_batch_size=2,
            per_device_eval_batch_size=2,
            learning_rate=0.01,
            use_cpu=True,
            report_to=[],
            save_strategy="no",
            eval_strategy="no",
            logging_steps=1,
            disable_tqdm=True,
            seed=42,
            data_seed=42,
        ),
    )
    callback.trainer = trainer
    evaluate = trainer.evaluate

    def evaluate_with_metrics(dataset, *, metric_key_prefix):
        # The SFT trainer separates training counters from evaluation counters by prefix.
        assert metric_key_prefix == "eval", "Reference checks must not drain training counters"
        telemetry = read_telemetry(root)
        assert telemetry["stage"] == "validation", (
            "Validation still reports completed sample construction"
        )
        assert telemetry["completed"] == 0 and telemetry["unit"] == "batches"
        return evaluate(dataset, metric_key_prefix=metric_key_prefix)

    with patch.object(trainer, "evaluate", side_effect=evaluate_with_metrics):
        trainer.train()
        callback.finish(trainer.state)
    if callback.monitor:
        events = [json.loads(line) for line in (root / "metrics.jsonl").read_text().splitlines()]
        plotted = [event for event in events if event["event"] == "BT_EVAL"]
        checks = callback.monitor.data["checks"]
        assert len(plotted) == len(checks), "Training-reference loss leaked into development chart"
        assert all(
            event["step"] == check["step"] and event["eval_loss"] == check["metrics"]["eval_loss"]
            for event, check in zip(plotted, checks, strict=True)
        )
    return {
        key: value.detach().clone() for key, value in model.named_parameters()
    }, callback.monitor


def native_run(root):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "overbae/services/sft_assets"))
    import decision_engine

    root.mkdir(parents=True, exist_ok=True)
    run_dir = root / "run"
    checkpoint = run_dir / "checkpoint-final"
    checkpoint.mkdir(parents=True, exist_ok=True)
    vocabulary = {f"t{i}": i for i in range(32)}
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=Tokenizer(WordLevel(vocabulary, unk_token="t0")),
        pad_token="t0",
        eos_token="t1",
        unk_token="t2",
    )
    config = LlamaConfig(
        vocab_size=32,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=1,
        num_attention_heads=2,
        num_key_value_heads=2,
        max_position_embeddings=32,
        pad_token_id=0,
        bos_token_id=2,
        eos_token_id=1,
    )
    config._name_or_path = "qualification/tiny-native"
    model = get_peft_model(
        LlamaForCausalLM(config),
        LoraConfig(
            r=2, lora_alpha=4, lora_dropout=0.1, target_modules=["q_proj"], task_type="CAUSAL_LM"
        ),
    )
    rows = [
        {
            "key": str(i),
            "kind": "choice",
            "input_ids": [2, 3, 4 + i],
            "option_token_ids": [14, 15],
            "target_probabilities": [0.25, 0.75],
            "weight": 2,
        }
        for i in range(8)
    ]
    for name in ("data.jsonl", "val.jsonl"):
        (root / name).write_text("".join(json.dumps(row) + "\n" for row in rows))
    (root / "preparation.json").write_text(
        json.dumps(
            {
                "objective": "decision_cross_entropy",
                "renderer": "fixture",
                "codebook": [],
                "vocab_fingerprint": "fixture",
            }
        )
    )
    previous = Path.cwd()
    try:
        os.chdir(root)
        with (
            patch.multiple(
                decision_engine,
                RUN_DIR=str(run_dir),
                CHECKPOINT_DIR=str(checkpoint),
                PER_DEVICE_BATCH=2,
                GRAD_ACCUM=1,
                N_EPOCHS=1,
                MAX_STEPS=0,
            ),
            patch.dict(
                os.environ,
                {
                    "BASE_MODEL_PATH": str(root),
                    "MODEL_ID": "qualification/tiny-native",
                    "DECISION_PRE_TRAINING_BASELINE": "1",
                    "TRAINING_MONITORING": json.dumps(
                        {
                            "mode": "steps",
                            "interval_steps": 1,
                            "failure_policy": "stop",
                            "loss_sample": 4,
                        }
                    ),
                },
            ),
            patch.object(
                decision_engine, "training_base_identity", return_value="fixture-sealed-base"
            ),
            patch.object(torch.cuda, "device_count", return_value=1),
        ):
            decision_engine.train(model, tokenizer)
        journal = json.loads((run_dir / "monitoring.json").read_text())
        assert all(check["state"] == "completed" for check in journal["checks"]), journal["checks"]
        for check in journal["checks"]:
            artifact = json.loads((run_dir / check["artifact"]["path"]).read_text())
            assert len(artifact["examples"]) == check["coverage"]["expected"]
            assert all(
                "target_probabilities" in row and "probabilities" in row and row["weight"] == 2
                for row in artifact["examples"]
            )
        assert len(journal["checkpoints"]) == 4
        assert all(item["verification"]["reload_verified"] for item in journal["checkpoints"])
        assert all(
            item["metrics"]["distribution_decisions"] == 4 for item in journal["checkpoints"]
        )
        return {
            "checks": journal["checks"],
            "checkpoints": journal["checkpoints"],
            "scope": "CPU native engine with PEFT probability replay; fixture base identity and GPU admission",
        }
    finally:
        os.chdir(previous)


def main():
    from modal_shared.training_monitoring import resolve_policy

    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.output)
    root.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(1)
    plain, _ = run(
        root / "off", resolve_policy({"mode": "off"}, has_development=True, provider="modal")
    )
    measured, monitor = run(
        root / "measured",
        resolve_policy(
            {"mode": "steps", "interval_steps": 2, "loss_sample": 4, "train_sample": 4},
            has_development=True,
            provider="modal",
        ),
    )
    difference = max((plain[key] - measured[key]).abs().max().item() for key in plain)
    checks = monitor.summary()["checks"]
    assert all(item["state"] == "completed" for item in checks), checks
    assert [item["step"] for item in checks] == [0, 2, 4, 6, 6]
    assert all(item["verification"]["reload_verified"] for item in monitor.summary()["checkpoints"])
    assert difference == 0, f"Monitoring changed the optimisation sequence: {difference}"
    assert monitor.summary()["numerical_health"]["logged_updates"] == 6
    assert monitor.summary()["numerical_health"]["nonfinite_gradient_observations"] == 0
    assert monitor.summary()["checks"][0]["facts"]["evaluation_batches"] > 0
    report = {
        "runtime": {"torch": torch.__version__},
        "steps": 6,
        "parameter_max_difference": difference,
        "checks": checks,
        "checkpoints": monitor.summary()["checkpoints"],
        "scope": "CPU Transformers/PEFT callback; not Unsloth/GPU qualification",
    }
    field_weights, field_monitor = run(
        root / "fields",
        resolve_policy(
            {
                "mode": "steps",
                "interval_steps": 2,
                "loss_sample": 4,
                "train_sample": 4,
                "generation_every": 1,
                "generation": {
                    "kind": "json_fields",
                    "fields": ["/risk"],
                    "sample": 4,
                    "max_new_tokens": 2,
                },
            },
            has_development=True,
            provider="modal",
        ),
        structured=True,
    )
    field_difference = max((plain[key] - field_weights[key]).abs().max().item() for key in plain)
    assert field_difference == 0, "Generated field checks changed the optimization sequence"
    for check in field_monitor.summary()["checks"]:
        assert check["state"] == "completed", check
        artifact = json.loads((root / "fields" / check["artifact"]["path"]).read_text())
        assert len(artifact["examples"]) == 4
        for example in artifact["examples"]:
            assert json.loads(example["reference"]) == {"risk": "high"}
            assert example["scorer"] == "json_fields:1"
            assert set(example["fields"]) == {"/risk"}
    report["fields"] = {
        "parameter_max_difference": field_difference,
        "checks": field_monitor.summary()["checks"],
        "scope": "Tiny CPU-generated JSON field checks; random model, not domain-quality evidence",
    }
    report["native"] = native_run(root / "native")
    (root / "result.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"result": str(root / "result.json"), "parameter_max_difference": difference}))


if __name__ == "__main__":
    main()
