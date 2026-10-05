import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import modal

from modal_shared.compute_usage import ComputeMeter
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
app = modal.App(
    RELEASE["app"],
    secrets=[modal.Secret.from_dict({"OVERMIND_EVALUATION_RELEASE": json.dumps(RELEASE)})],
)
volume = modal.Volume.from_name("overmind-sft")
weights = modal.Volume.from_name("overmind-weights")
image = TRAIN_IMAGES[TRAIN_U2026_8_18]
assets = Path("/root/sft_assets")


def paths(evaluation_id, model_run):
    if any(
        re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
        for value in (evaluation_id, model_run)
        if value is not None
    ):
        raise ValueError("Evaluation and model identifiers must be path components")
    return Path("/data/decision-evaluations") / evaluation_id, Path(
        "/data/runs"
    ) / model_run if model_run else None


@app.function(
    image=image,
    cpu=8,
    memory=8192,
    timeout=23 * 3600,
    volumes={"/data": volume, "/weights": weights},
)
def prepare(evaluation_id, model_run, input_sha256, *, foundation=None, inference=None):
    meter = ComputeMeter(cpu=8, memory_gib=8)
    volume.reload()
    directory, model = paths(evaluation_id, model_run)
    if model is None:
        if not foundation or not foundation.get("hf_model"):
            raise ValueError("Select a catalog foundation or a trained artifact")
        fetch = modal.Function.from_name("overmind-register", "fetch_base_model")
        call_path = directory / "base-call.json"
        if call_path.exists():
            base = modal.FunctionCall.from_id(json.loads(call_path.read_text())["id"]).get()
        else:
            call = fetch.spawn(base_model=foundation["hf_model"])
            atomic_json(call_path, {"id": call.object_id})
            volume.commit()
            base = call.get()
        weights.reload()
        request = {
            "artifact_identity": "base:" + base["base_identity"],
            "input_sha256": input_sha256,
            "foundation": {
                **foundation,
                "base_path": base["base_model_path"],
                "base_identity": base["base_identity"],
            },
            "context_length": (inference or {}).get("context_length", 8192),
        }
    else:
        artifact = read_artifact(model / "final")
        request = {"artifact_identity": artifact["identity"], "input_sha256": input_sha256}
    atomic_json(directory / "request.json", request)
    with (directory / "prepare.log").open("a") as log:
        subprocess.run(
            [
                sys.executable,
                str(assets / "prepare_decisions.py"),
                str(directory),
                *([str(model)] if model else []),
            ],
            check=True,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    report = json.loads((directory / "preparation.json").read_text())
    report["compute_usage"] = meter.snapshot()
    atomic_json(directory / "preparation.json", report)
    volume.commit()
    return report


def prediction_environment(directory, model, work, base_only, inference):
    report = json.loads((directory / "preparation.json").read_text())
    artifact = read_artifact(model / "final") if model else None
    if artifact and report["artifact_identity"] != artifact["identity"]:
        raise ValueError("Prepared inputs belong to another decision artifact")
    tokens = directory / "tokens.jsonl"
    if digest_file(tokens) != report["tokens_sha256"]:
        raise ValueError("Prepared decision inputs changed")
    training = (
        artifact["training"]
        if artifact
        else {
            "seed": 0,
            "model_config": {
                "MODEL_ID": report["foundation"]["hf_model"],
                "BASE_MODEL_PATH": report["foundation"]["base_path"],
                "UNSLOTH_IMAGE": TRAIN_U2026_8_18,
                "TRAINING_TYPE": "Lora",
                "LOAD_IN_4BIT": "0",
            },
        }
    )
    if training["model_config"]["UNSLOTH_IMAGE"] != TRAIN_U2026_8_18:
        raise ValueError("Decision evaluation requires the qualified training stack")
    shutil.copy2(directory / "preparation.json", work / "preparation.json")
    shutil.copytree(model / "final" if model else directory / "tokenizer", work / "tokenizer")
    env = {
        **os.environ,
        **{k: v for k, v in training["model_config"].items() if v is not None},
        "TRAINING_OBJECTIVE": "decision_cross_entropy",
        "MAX_LENGTH": str(report["context_length"]),
        "SEED": str(training["seed"]),
        "PER_DEVICE_BATCH": str(inference["batch_size"]),
        "GRAD_ACCUM": "1",
        "PADDED_TOKEN_BUDGET": str(max(report["max_tokens"], inference["padded_tokens"])),
        "BT_CHECKPOINT_DIR": str(model / "final" if model else directory),
        "BT_RUN_DIR": str(work),
        "DECISION_INPUTS_PATH": str(tokens),
        "DECISION_BASE_ONLY": "1" if base_only else "0",
        "DECISION_FOUNDATION_PATH": str(directory / "preparation.json") if model is None else "",
    }
    return env


@app.function(
    image=image,
    gpu="H100",
    timeout=24 * 3600,
    volumes={"/data": volume, "/weights": weights},
)
def predict(evaluation_id, model_run, *, base_only=False, inference=None):
    meter = ComputeMeter(gpu_type="H100", gpu_count=1)
    volume.reload()
    weights.reload()
    directory, model = paths(evaluation_id, model_run)
    report = json.loads((directory / "preparation.json").read_text())
    label = "base" if base_only else "candidate"
    result = directory / f"{label}.jsonl"
    inference = inference or {"batch_size": 64, "padded_tokens": 32768}
    with tempfile.TemporaryDirectory(prefix="decision-predictions-") as temporary:
        work = Path(temporary)
        env = prediction_environment(directory, model, work, base_only, inference)
        env["DECISION_OUTPUT_PATH"] = str(result)
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
        "compute_usage": meter.snapshot(),
        "decisions": count,
        "artifact_identity": report["artifact_identity"],
        "base_only": base_only,
        "input_sha256": report["input_sha256"],
        "predictions_sha256": digest_file(result),
        "runtime_fingerprint": training_fingerprint(assets),
    }
    atomic_json(directory / f"{label}.json", receipt)
    volume.commit()
    return receipt


@app.cls(
    image=image,
    gpu="H100",
    timeout=1200,
    scaledown_window=120,
    max_containers=1,
    volumes={"/data": volume, "/weights": weights},
)
@modal.concurrent(max_inputs=16)
class DecisionPerformanceEndpoint:
    evaluation_id: str = modal.parameter()
    model_run: str = modal.parameter()
    base_only: bool = modal.parameter()
    measurement_id: str = modal.parameter()

    @modal.enter()
    def load(self):
        self.meter = ComputeMeter(gpu_type="H100", gpu_count=1)
        volume.reload()
        weights.reload()
        directory, model = paths(self.evaluation_id, self.model_run or None)
        self.temporary = tempfile.TemporaryDirectory(prefix="decision-performance-")
        self.work = Path(self.temporary.name)
        self.worker_instance = self.work.name
        self.socket_path = self.work / "readout.sock"
        env = prediction_environment(
            directory, model, self.work, self.base_only, {"batch_size": 16, "padded_tokens": 32768}
        )
        env["DECISION_SERVICE_SOCKET"] = str(self.socket_path)
        self.log = (self.work / "worker.log").open("w")
        self.process = subprocess.Popen(
            [sys.executable, str(assets / "train.py")],
            cwd=self.work,
            env=env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 900
        while not self.socket_path.exists():
            if self.process.poll() is not None or time.monotonic() >= deadline:
                raise RuntimeError("The isolated performance worker did not become ready")
            time.sleep(0.1)

    @modal.method()
    def predict(self, requests):
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(900)
            connection.connect(str(self.socket_path))
            connection.sendall(json.dumps(requests).encode() + b"\n")
            with connection.makefile("rb") as source:
                result = json.loads(source.readline(32 * 1024 * 1024))
        if "error" in result:
            raise ValueError(result["error"])
        return {
            **result,
            "compute_usage": self.meter.snapshot(),
            "runtime_fingerprint": training_fingerprint(assets),
            "worker_instance": self.worker_instance,
            "hardware": "H100",
            "batch_size": 16,
            "padded_tokens": 32768,
            "execution": "sequential native readout with client concurrency",
            "kv_cache": "disabled",
            "measurement_id": self.measurement_id,
        }

    @modal.exit()
    def stop(self):
        if hasattr(self, "process"):
            self.process.terminate()
            self.process.wait(timeout=20)
        if hasattr(self, "log"):
            self.log.close()
        if hasattr(self, "temporary"):
            self.temporary.cleanup()
