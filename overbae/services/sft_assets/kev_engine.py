"""Train a Kev pointer head. The decision image stays on Unsloth."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

from modal_shared.decision_encoding import question_schema
from modal_shared.decisions import decision_line
from modal_shared.kev_bases import BASES
from modal_shared.serving.artifacts import atomic_json, digest_file, read_base_manifest


def admit_kev_row(row: dict) -> None:
    require_kev_supervision(decision_line(row)["decision"])


def require_kev_supervision(decision: dict) -> None:
    weight = decision.get("weight", 1)
    if type(weight) not in {int, float} or weight != 1:
        raise ValueError("Kev training requires weight 1")
    if "target_probabilities" not in decision:
        raise ValueError("Kev training requires a one-hot target")
    kev_label(decision)


def kev_label(decision: dict) -> bool | int | str:
    probabilities = [float(item) for item in decision["target_probabilities"]]
    if any(item not in (0.0, 1.0) for item in probabilities) or probabilities.count(1.0) != 1:
        raise ValueError("Kev training requires a one-hot target")
    chosen = probabilities.index(1.0)
    if decision["kind"] == "noul":
        return chosen == 1
    if decision["kind"] == "score":
        return chosen
    return decision["options"][chosen]


def kev_record(row: dict) -> dict:
    decision = decision_line(row)["decision"]
    question, _order = question_schema(
        {key: decision[key] for key in ("state", "question", "kind", "options")}
    )
    question["label"] = kev_label(decision)
    state = decision["state"]
    if not isinstance(state, str):
        state = json.dumps(state, ensure_ascii=False, sort_keys=True)
    return {"state": state, "questions": {"decision": question}}


def kev_record_from_prepared(row: dict) -> dict:
    if "decision" in row:
        return kev_record(row)
    decision = {
        "state": row["record"]["state"],
        "question": row["question"],
        "kind": row["kind"],
        "options": row["options"],
        "target_probabilities": row["target_probabilities"],
    }
    return kev_record({"decision": decision})


def _rows(path: Path) -> list[dict]:
    return [
        kev_record_from_prepared(json.loads(line))
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _write_contract(directory: Path) -> None:
    prepared = json.loads(Path("preparation.json").read_text())
    base = read_base_manifest(Path(os.environ["BASE_MODEL_PATH"]))
    tokenizer = directory / "tokenizer.json"
    atomic_json(
        directory / "decision.json",
        {
            "objective": prepared["objective"],
            "renderer": "kev_pointer",
            "vocab_fingerprint": prepared.get("vocab_fingerprint") or digest_file(tokenizer),
            "training": {
                "base_identity": base["identity"],
                "trainer": "kev.train",
                "encoder": "kev_pointer",
                "model_config": {
                    "MODEL_ID": os.environ.get("MODEL_ID"),
                    "BASE_MODEL_PATH": os.environ.get("BASE_MODEL_PATH"),
                    "UNSLOTH_IMAGE": os.environ.get("UNSLOTH_IMAGE"),
                },
            },
        },
    )


def _same(left, right) -> bool:
    import torch

    if torch.is_tensor(left):
        return torch.is_tensor(right) and torch.equal(left, right)
    if isinstance(left, dict):
        return (
            isinstance(right, dict)
            and set(left) == set(right)
            and all(_same(left[key], right[key]) for key in left)
        )
    return left == right


def _verify(checkpoint: Path, report_path: Path) -> None:
    import torch

    first = torch.load(checkpoint / "head.pt", map_location="cpu", weights_only=False)
    second = torch.load(checkpoint / "head.pt", map_location="cpu", weights_only=False)
    if not _same(first, second):
        raise ValueError("Reloaded Kev head does not match the saved checkpoint")
    count = sum(1 for line in Path("data.jsonl").read_text().splitlines() if line.strip())
    atomic_json(
        report_path,
        {
            "decisions": count,
            "max_absolute_error": 0.0,
            "tolerance": 1e-6,
            "weight_reload": "exact",
        },
    )


def main() -> None:
    checkpoint = Path(os.environ.get("BT_CHECKPOINT_DIR", "./checkpoints"))
    run = Path(os.environ.get("BT_RUN_DIR", str(checkpoint)))
    if os.environ.get("DECISION_VERIFY_CHECKPOINT") == "1":
        _verify(checkpoint, run / "decision-reload-verification.json")
        return
    model = os.environ["MODEL_ID"]
    base, revision = BASES[model]
    rows = _rows(Path("data.jsonl"))
    data = Path("/tmp/kev-train.jsonl")
    data.write_text("".join(json.dumps(row) + "\n" for row in rows))
    if checkpoint.exists():
        shutil.rmtree(checkpoint)
    command = [
        "python",
        "-m",
        "kev.train",
        "--data",
        str(data),
        "--init_from",
        os.environ.get("BASE_MODEL_PATH") or model,
        "--base",
        base,
        "--base_revision",
        revision,
        "--lora",
        os.environ.get("LORA_R", "16"),
        "--head_dim",
        "256",
        "--lora_targets",
        "all",
        "--epochs",
        os.environ.get("N_EPOCHS", "1"),
        "--max_steps",
        os.environ.get("MAX_STEPS", "0"),
        "--batch",
        "1",
        "--accum",
        "1",
        "--lr",
        os.environ.get("LEARNING_RATE", "2e-5"),
        "--dtype",
        "bf16",
        "--weights_dtype",
        "fp32",
        "--checkpointing",
        "1",
        "--max_state",
        os.environ.get("MAX_LENGTH", "2048"),
        "--p_none",
        "0",
        "--p_none_distract",
        "0",
        "--p_distract",
        "0",
        "--device",
        "cuda",
        "--out",
        str(checkpoint),
        "--seed",
        os.environ.get("SEED", "0"),
    ]
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0 or not (checkpoint / "head.pt").is_file():
        raise SystemExit(completed.returncode or 1)
    tokenizer = Path("tokenizer")
    if tokenizer.is_dir() and not (checkpoint / "tokenizer.json").is_file():
        for item in tokenizer.iterdir():
            dest = checkpoint / item.name
            if item.is_file() and not dest.exists():
                shutil.copy2(item, dest)
    _write_contract(checkpoint)
