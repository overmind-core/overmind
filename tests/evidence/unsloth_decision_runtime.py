"""Run in the pinned decision image: real trainer, no provider or tensor mocks."""

import argparse
import json
import os
import signal
import subprocess
import sys
from pathlib import Path

from unsloth import FastDecisionModel  # isort: skip

import torch
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from transformers import LlamaConfig, LlamaForCausalLM, PreTrainedTokenizerFast
from unsloth.models.clef import encode_record

from modal_shared.decision_artifact import seal_artifact
from modal_shared.decision_encoding import DecisionEncoder
from modal_shared.decision_inference import input_digest
from modal_shared.serving.artifacts import atomic_json


def run(root, assets, semantics_only=False):
    root.mkdir(parents=True, exist_ok=True)
    base, work, run_dir = root / "base", root / "work", root / "run"
    base.mkdir(exist_ok=True)
    work.mkdir(exist_ok=True)
    vocabulary = {
        word: i
        for i, word in enumerate(
            ["<unk>", "<pad>", "<eos>"]
            + [chr(i) for i in range(33, 127)]
            + ["good", "bad", "positive", "negative", "true", "false", "question", "state"]
        )
    }
    raw = Tokenizer(WordLevel(vocabulary, unk_token="<unk>"))
    raw.pre_tokenizer = Whitespace()
    tokenizer = PreTrainedTokenizerFast(
        tokenizer_object=raw, unk_token="<unk>", pad_token="<pad>", eos_token="<eos>"
    )
    tokenizer.save_pretrained(base)
    torch.manual_seed(42)
    LlamaForCausalLM(
        LlamaConfig(
            vocab_size=len(tokenizer),
            hidden_size=64,
            intermediate_size=128,
            num_hidden_layers=1,
            num_attention_heads=2,
            num_key_value_heads=2,
            max_position_embeddings=2048,
            pad_token_id=tokenizer.pad_token_id,
            eos_token_id=tokenizer.eos_token_id,
        )
    ).save_pretrained(base)
    model, loaded = FastDecisionModel.from_pretrained(
        str(base),
        max_seq_length=1024,
        head_width=64,
        load_in_4bit=False,
        random_state=42,
        use_gradient_checkpointing=False,
    )
    assert model.is_clef and any(p.requires_grad for p in model.head.parameters())
    sys.path.insert(0, str(assets))
    from decision_tokenizer import encode_record as cpu_encode

    encoder = DecisionEncoder(tokenizer, encode_record)
    cpu_encoder = DecisionEncoder(tokenizer, cpu_encode)
    rows = []
    for i in range(12):
        decision = {
            "state": "good" if i % 2 else "bad",
            "question": "positive?",
            "kind": "noul",
            "options": ["negative", "positive"],
            "target_probabilities": [0.2, 0.8] if i % 2 else [1.0, 0.0],
            "weight": float(i % 3 + 1),
        }
        if i % 4 == 0:
            decision.update(
                kind="score",
                options=["bad", "neutral", "good"],
                target_mean=0.4,
                option_values=[-1.0, 0.0, 2.0],
                target_semantics="ordinal_mean",
            )
            decision.pop("target_probabilities")
        elif i % 4 == 1:
            decision.update(kind="choice", options=["positive", "negative"])
        row = encoder.training_row({"decision": decision})
        assert row == cpu_encoder.training_row({"decision": decision})
        assert row.get("target_probabilities") == decision.get("target_probabilities")
        assert row.get("target_mean") == decision.get("target_mean")
        rows.append(
            {
                "key": str(i),
                "input_sha256": input_digest(
                    {k: decision[k] for k in ("state", "question", "kind", "options")}
                ),
                **row,
            }
        )
    from decision_readout import (
        collate,
        configure_decision_head,
        decision_logits,
        loss_terms,
        probability_vectors,
    )
    from preprocess import preprocess_rows

    batch = collate(rows, tokenizer.pad_token_id, "cuda")
    configure_decision_head(model)
    with torch.no_grad():
        first = decision_logits(model.eval(), batch)
        second = decision_logits(model, batch)
    torch.testing.assert_close(first, second, atol=0, rtol=0)
    logits = decision_logits(model.eval(), batch).detach().float().requires_grad_()
    numerator, denominator = loss_terms(logits, batch)
    (numerator / denominator).backward()
    expected = logits.grad.clone()
    logits.grad = None
    for start, end in ((0, 5), (5, 12)):
        part = collate(rows[start:end], tokenizer.pad_token_id, "cuda")
        term, _ = loss_terms(logits[start:end], part)
        (term / denominator).backward()
    torch.testing.assert_close(logits.grad, expected, atol=1e-7, rtol=1e-6)
    mean = collate([rows[0]], tokenizer.pad_token_id, "cuda")
    for p in ([0.2, 0.5, 0.3], [0.4, 0.2, 0.4]):
        term, _ = loss_terms(torch.tensor([p], device="cuda").log(), mean)
        assert term.item() < 1e-12
    for row in rows:
        part = collate([row], tokenizer.pad_token_id, "cuda")
        original = torch.arange(len(row["options"]), dtype=torch.float32, device="cuda")
        ordered = original[row["option_order"]].unsqueeze(0)
        actual = probability_vectors(ordered, part)[0]["probabilities"]
        torch.testing.assert_close(torch.tensor(actual), original.softmax(0).cpu())
    example = {
        "decision": {
            "state": "good",
            "question": "positive?",
            "kind": "noul",
            "options": ["negative", "positive"],
            "target_probabilities": [0.2, 0.8],
        }
    }
    prepared, report = preprocess_rows(
        [example],
        tokenizer,
        "qualification/tiny-llama",
        1024,
        None,
        objective="decision_cross_entropy",
        decision_encode=cpu_encode,
    )
    assert report["supervised_decisions"] == 1 and "supervised_tokens" not in report
    assert prepared[0]["target_probabilities"] == [0.2, 0.8]
    _, overflow = preprocess_rows(
        [example],
        tokenizer,
        "qualification/tiny-llama",
        8,
        None,
        objective="decision_cross_entropy",
        decision_encode=cpu_encode,
    )
    assert overflow["incompatible_rows"] == 1 and overflow["supervised_decisions"] == 0
    if semantics_only:
        atomic_json(
            root / "result.json",
            {
                "weighted_accumulation": "passed",
                "mean_target_distributions": 2,
                "option_orders": len(rows),
                "preparation_and_overflow": "passed",
                "cpu_gpu_encoder_parity": len(rows),
                "first_call_max_error": (first - second).abs().max().item(),
            },
        )
        return
    del model, loaded
    torch.cuda.empty_cache()
    for filename in ("data.jsonl", "val.jsonl"):
        (work / filename).write_text("".join(json.dumps(row) + "\n" for row in rows))
    import hashlib

    atomic_json(
        work / "preparation.json",
        {
            "objective": "decision_supervised",
            "renderer": "unsloth_clef",
            "vocab_fingerprint": hashlib.sha256(
                json.dumps(tokenizer.get_vocab(), sort_keys=True).encode()
            ).hexdigest(),
            "context_length": 1024,
        },
    )
    tokenizer.save_pretrained(work / "tokenizer")
    # A real immutable base manifest is normally supplied by fetch_base_model.
    from modal_shared.serving.artifacts import seal_base

    seal_base(base, "qualification/tiny-llama")
    env = {
        **os.environ,
        "MODEL_ID": "qualification/tiny-llama",
        "BASE_MODEL_PATH": str(base),
        "TRAINING_OBJECTIVE": "decision_supervised",
        "UNSLOTH_IMAGE": "u2026_10_3_decision",
        "TRAINING_TYPE": "Lora",
        "LOAD_IN_4BIT": "0",
        "LORA_R": "4",
        "LORA_ALPHA": "8",
        "LORA_TARGET_MODULES": "q_proj,v_proj",
        "LORA_DROPOUT": "0",
        "DECISION_HEAD_WIDTH": "64",
        "MAX_LENGTH": "1024",
        "N_EPOCHS": "2",
        "MAX_STEPS": "4",
        "PER_DEVICE_BATCH": "2",
        "GRAD_ACCUM": "2",
        "SEED": "42",
        "LEARNING_RATE": "0.001",
        "BT_RUN_DIR": str(run_dir),
        "BT_CHECKPOINT_DIR": str(run_dir / "checkpoint-final"),
        "TRAINING_MONITORING": json.dumps(
            {
                "mode": "steps",
                "interval_steps": 2,
                "initial": os.environ.get("DECISION_PRE_TRAINING_BASELINE", "1") == "1",
                "final": True,
                "loss_sample": 12,
                "train_sample": 4,
            }
        ),
    }
    for verify in ("0", "1"):
        subprocess.run(
            [sys.executable, str(assets / "train.py")],
            cwd=work,
            env={**env, "DECISION_VERIFY_CHECKPOINT": verify},
            check=True,
        )
    verification = json.loads((run_dir / "decision-reload-verification.json").read_text())
    assert verification["max_absolute_error"] <= verification["tolerance"]
    assert verification["weight_reload"] == "exact"
    telemetry = json.loads((run_dir / "telemetry.json").read_text())
    assert telemetry["stage"] == "checkpoint_reload"
    assert telemetry["completed"] == telemetry["total"] == 12
    assert telemetry["unit"] == "decisions"
    if env.get("DECISION_PRE_TRAINING_BASELINE") == "0":
        assert not (run_dir / "decision-before.json").exists()
        assert telemetry["pre_training_baseline"]["status"] == "not_requested"
    corrupt = root / "check_corruption.py"
    corrupt.write_text(
        "from unsloth import FastDecisionModel\n"
        "import torch, sys\n"
        f"sys.path.insert(0, {str(assets)!r})\n"
        "from decision_engine import verify_weights\n"
        f"path = {str(run_dir / 'checkpoint-final')!r}\n"
        "model, _ = FastDecisionModel.from_pretrained(path, max_seq_length=1024, load_in_4bit=False)\n"
        "verify_weights(model, path)\n"
        "with torch.no_grad(): next(model.head.parameters()).view(-1)[0].add_(0.001)\n"
        "try: verify_weights(model, path)\n"
        "except ValueError: pass\n"
        "else: raise AssertionError('Changed head tensor accepted')\n"
    )
    subprocess.run([sys.executable, str(corrupt)], cwd=work, env=env, check=True)
    seal_artifact(run_dir / "checkpoint-final", verification)
    after = json.loads((run_dir / "decision-after.json").read_text())
    assert after["mean_decisions"] == 3 and after["distribution_decisions"] == 9
    assert after["hard_label_decisions"] == 3
    resumed = root / "resumed"
    resumed_env = {
        **env,
        "BT_RUN_DIR": str(resumed),
        "BT_CHECKPOINT_DIR": str(resumed / "checkpoint-final"),
    }
    interrupt = root / "interrupt.py"
    interrupt.write_text(
        "import os, signal, sys\n"
        f"sys.path.insert(0, {str(assets)!r})\n"
        "import decision_engine as engine\n"
        "original = engine.DecisionCallbacks.on_save\n"
        "def stop(self, args, state, control, **kwargs):\n"
        "    original(self, args, state, control, **kwargs)\n"
        "    if state.global_step == 2: os.kill(os.getpid(), signal.SIGKILL)\n"
        "engine.DecisionCallbacks.on_save = stop\n"
        "engine.main()\n"
    )
    killed = subprocess.run([sys.executable, str(interrupt)], cwd=work, env=resumed_env)
    assert killed.returncode == -signal.SIGKILL
    assert (resumed / "recovery/checkpoint-2/overmind-recovery.json").exists()
    (resumed / "recovery/checkpoint-3").mkdir()
    (resumed / "recovery/checkpoint-3/adapter_model.safetensors").write_bytes(b"interrupted write")
    subprocess.run(
        [sys.executable, str(assets / "train.py")], cwd=work, env=resumed_env, check=True
    )
    subprocess.run(
        [sys.executable, str(assets / "train.py")], cwd=work, env=resumed_env, check=True
    )
    recovered_state = json.loads((resumed / "recovery/checkpoint-4/trainer_state.json").read_text())
    assert recovered_state["global_step"] == 4
    assert not (resumed / "recovery/checkpoint-5").exists()
    from safetensors.torch import load_file

    maximum = 0.0
    for filename in ("adapter_model.safetensors", "joint_head.safetensors"):
        expected = load_file(str(run_dir / "checkpoint-final" / filename))
        actual = load_file(str(resumed / "checkpoint-final" / filename))
        assert expected.keys() == actual.keys()
        for name in expected:
            torch.testing.assert_close(actual[name], expected[name], atol=1e-6, rtol=1e-5)
            maximum = max(maximum, (actual[name] - expected[name]).abs().max().item())
    from modal_shared.decisions import TARGET_FIELDS

    requests = []
    for row in rows:
        inputs = {k: v for k, v in row.items() if k not in {*TARGET_FIELDS, "weight"}}
        requests.append(inputs)
    request_path = work / "inputs.jsonl"
    request_path.write_text("".join(json.dumps(r) + "\n" for r in requests))
    subprocess.run(
        [sys.executable, str(assets / "train.py")],
        cwd=work,
        env={
            **env,
            "DECISION_INPUTS_PATH": str(request_path),
            "DECISION_OUTPUT_PATH": str(root / "predictions.jsonl"),
        },
        check=True,
    )
    predictions = [
        json.loads(line) for line in (root / "predictions.jsonl").read_text().splitlines()
    ]
    assert len(predictions) == len(rows)
    references = {
        r["key"]: r
        for r in map(
            json.loads, (run_dir / "decision-reload-reference.jsonl").read_text().splitlines()
        )
    }
    for prediction in predictions:
        assert not set(prediction) & set(TARGET_FIELDS)
        torch.testing.assert_close(
            torch.tensor(prediction["probabilities"]),
            torch.tensor(references[prediction["key"]]["probabilities"]),
            atol=1e-4,
            rtol=0,
        )
    result = {
        "verification": verification,
        "metrics": after,
        "recovery": {
            "interrupted_step": 2,
            "completed_resume_step": recovered_state["global_step"],
            "max_parameter_error": maximum,
        },
        "input_only_predictions": len(predictions),
        "first_call_max_error": (first - second).abs().max().item(),
        "pre_training_baseline": env.get("DECISION_PRE_TRAINING_BASELINE", "1") == "1",
        "torch": torch.__version__,
        "gpu": torch.cuda.get_device_name(0),
    }
    atomic_json(root / "result.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--semantics-only", action="store_true")
    args = parser.parse_args()
    run(args.root.resolve(), args.assets.resolve(), args.semantics_only)
