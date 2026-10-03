import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import modal

from modal_shared.decision_artifact import read_artifact
from modal_shared.images.train import TRAIN_IMAGES
from modal_shared.preparation import training_fingerprint
from modal_shared.serving.artifacts import atomic_json, digest_file
from modal_shared.stacks import TRAIN_U2026_8_18
from modal_shared.training_release import evaluation_identity

RELEASE = (
    evaluation_identity(Path(__file__).resolve().parents[2])
    if modal.is_local()
    else json.loads(os.environ["OVERMIND_EVALUATION_RELEASE"])
)
app = modal.App(RELEASE["app"])
volume = modal.Volume.from_name("overmind-sft")
weights = modal.Volume.from_name("overmind-weights")
image = TRAIN_IMAGES[TRAIN_U2026_8_18].env({"OVERMIND_EVALUATION_RELEASE": json.dumps(RELEASE)})
assets = Path("/root/sft_assets")


def paths(evaluation_id, model_run):
    if any(re.fullmatch(r"[A-Za-z0-9_-]+", value) is None for value in (evaluation_id, model_run)):
        raise ValueError("Evaluation and model identifiers must be path components")
    return Path("/data/decision-evaluations") / evaluation_id, Path("/data/runs") / model_run


@app.function(image=image, cpu=8, memory=8192, timeout=23 * 3600, volumes={"/data": volume})
def prepare(evaluation_id, model_run, input_sha256):
    volume.reload()
    directory, model = paths(evaluation_id, model_run)
    artifact = read_artifact(model / "final")
    request = {"artifact_identity": artifact["identity"], "input_sha256": input_sha256}
    atomic_json(directory / "request.json", request)
    with (directory / "prepare.log").open("a") as log:
        subprocess.run(
            [sys.executable, str(assets / "prepare_decisions.py"), str(directory), str(model)],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    report = json.loads((directory / "preparation.json").read_text())
    volume.commit()
    return report


@app.function(
    image=image,
    gpu="H100",
    timeout=24 * 3600,
    volumes={"/data": volume, "/weights": weights},
)
def predict(evaluation_id, model_run, *, base_only=False):
    volume.reload()
    weights.reload()
    directory, model = paths(evaluation_id, model_run)
    artifact = read_artifact(model / "final")
    report = json.loads((directory / "preparation.json").read_text())
    if report["artifact_identity"] != artifact["identity"]:
        raise ValueError("Prepared inputs belong to another decision artifact")
    tokens = directory / "tokens.jsonl"
    if digest_file(tokens) != report["tokens_sha256"]:
        raise ValueError("Prepared decision inputs changed")
    training = artifact["training"]
    if training["model_config"]["UNSLOTH_IMAGE"] != TRAIN_U2026_8_18:
        raise ValueError("Decision evaluation requires the qualified training stack")
    label = "base" if base_only else "candidate"
    result = directory / f"{label}.jsonl"
    with tempfile.TemporaryDirectory(prefix="decision-predictions-") as temporary:
        work = Path(temporary)
        shutil.copy2(model / "preparation.json", work / "preparation.json")
        shutil.copytree(model / "final", work / "tokenizer")
        env = {
            **os.environ,
            **{k: v for k, v in training["model_config"].items() if v is not None},
            "TRAINING_OBJECTIVE": "decision_cross_entropy",
            "MAX_LENGTH": str(report["context_length"]),
            "SEED": str(training["seed"]),
            "PER_DEVICE_BATCH": "64",
            "GRAD_ACCUM": "1",
            "PADDED_TOKEN_BUDGET": str(max(report["max_tokens"], 32768)),
            "BT_CHECKPOINT_DIR": str(model / "final"),
            "BT_RUN_DIR": str(work),
            "DECISION_INPUTS_PATH": str(tokens),
            "DECISION_OUTPUT_PATH": str(result),
            "DECISION_BASE_ONLY": "1" if base_only else "0",
        }
        with (directory / f"{label}.log").open("a") as log:
            subprocess.run(
                [sys.executable, str(assets / "train.py")],
                cwd=work,
                env=env,
                check=True,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
    with result.open() as stream:
        count = sum(1 for _ in stream)
    if count != report["ready_decisions"]:
        raise ValueError("Prediction coverage differs from the prepared inputs")
    receipt = {
        "decisions": count,
        "artifact_identity": artifact["identity"],
        "base_only": base_only,
        "input_sha256": report["input_sha256"],
        "predictions_sha256": digest_file(result),
        "runtime_fingerprint": training_fingerprint(assets),
    }
    atomic_json(directory / f"{label}.json", receipt)
    volume.commit()
    return receipt
