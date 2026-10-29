import json
import math
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

from modal_shared.decision_artifact import read_artifact
from modal_shared.decision_inference import input_digest
from modal_shared.decisions import decision_request
from modal_shared.images.train import TRAIN_IMAGES
from modal_shared.operational_events import STORE, Journal
from modal_shared.serving.artifacts import atomic_json, read_base_manifest
from modal_shared.stacks import TRAIN_DECISION
from modal_shared.training_release import serving_identity

RELEASE = (
    serving_identity(Path(__file__).resolve().parents[2])
    if modal.is_local()
    else json.loads(os.environ["OVERMIND_SERVING_RELEASE"])
)
app = modal.App(
    RELEASE["app"],
    secrets=[modal.Secret.from_dict({"OVERMIND_SERVING_RELEASE": json.dumps(RELEASE)})],
)
weights = modal.Volume.from_name("overmind-weights")
training = modal.Volume.from_name("overmind-sft")
image = TRAIN_IMAGES[TRAIN_DECISION]
assets = Path("/root/sft_assets")


def artifact_path(identity):
    if not re.fullmatch(r"[0-9a-f]{64}", identity):
        raise ValueError("Invalid artifact identity")
    return Path("/weights/.decisions") / identity


@app.function(image=image, timeout=1800, volumes={"/data": training, "/weights": weights})
def publish(remote_id, artifact_identity):
    run = remote_id.split(":", 1)[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run):
        raise ValueError("Invalid training run")
    training.reload()
    weights.reload()
    destination = artifact_path(artifact_identity)
    if not destination.exists():
        source = Path("/data/runs") / run / "final"
        if read_artifact(source)["identity"] != artifact_identity:
            raise ValueError("Training artifact identity differs")
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=destination.parent) as temporary:
            staged = Path(temporary) / "checkpoint"
            shutil.copytree(source, staged)
            if read_artifact(staged)["identity"] != artifact_identity:
                raise ValueError("Serving copy differs from the checkpoint")
            # Concurrent publication may only reuse the identical immutable artifact.
            try:
                staged.rename(destination)
            except OSError:
                if not destination.exists():
                    raise
    artifact = read_artifact(destination)
    if artifact["identity"] != artifact_identity:
        raise ValueError("Serving artifact identity differs")
    base = Path(artifact["training"]["model_config"]["BASE_MODEL_PATH"])
    if read_base_manifest(base)["identity"] != artifact["training"]["base_identity"]:
        raise ValueError("Serving base differs from the trained base")
    weights.commit()
    return {"weights_path": str(destination), "artifact_identity": artifact_identity}


@app.cls(
    image=image,
    gpu="H100",
    timeout=1200,
    scaledown_window=120,
    max_containers=1,
    volumes={"/weights": weights},
)
@modal.concurrent(max_inputs=16)
class DecisionEndpoint:
    weights_path: str = modal.parameter()
    artifact_identity: str = modal.parameter()
    max_model_len: int = modal.parameter()
    # Empty stays the shared serverless pool. A dedicated id is its own pool,
    # so its autoscaler cannot pin every decision of this artifact.
    pool: str = modal.parameter(default="")

    @modal.enter()
    def load(self):
        weights.reload()
        checkpoint = artifact_path(self.artifact_identity)
        if str(checkpoint) != self.weights_path:
            raise ValueError("Serving path differs from the pinned artifact")
        artifact = read_artifact(checkpoint)
        if artifact["identity"] != self.artifact_identity:
            raise ValueError("Serving identity differs")
        config = artifact["training"]["model_config"]
        base = read_base_manifest(Path(config["BASE_MODEL_PATH"]))
        if base["identity"] != artifact["training"]["base_identity"]:
            raise ValueError("Serving base identity differs")
        if config["UNSLOTH_IMAGE"] != TRAIN_DECISION:
            raise ValueError("Decision artifact requires another runtime")
        self.temporary = tempfile.TemporaryDirectory(prefix="decision-serving-")
        self.work = Path(self.temporary.name)
        self.socket_path = self.work / "readout.sock"
        atomic_json(
            self.work / "preparation.json",
            {
                "context_length": self.max_model_len,
                "vocab_fingerprint": artifact["vocab_fingerprint"],
                "renderer": artifact["renderer"],
            },
        )
        env = {
            **os.environ,
            **{key: str(value) for key, value in config.items() if value is not None},
            "TRAINING_OBJECTIVE": artifact["objective"],
            "MAX_LENGTH": str(self.max_model_len),
            "SEED": str(artifact["training"]["seed"]),
            "BT_CHECKPOINT_DIR": str(checkpoint),
            "BT_RUN_DIR": str(self.work),
            "PER_DEVICE_BATCH": "16",
            "PADDED_TOKEN_BUDGET": str(max(32768, self.max_model_len)),
            "DECISION_SERVICE_SOCKET": str(self.socket_path),
        }
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
                raise RuntimeError("Decision serving worker did not become ready")
            time.sleep(0.1)

    @modal.method()
    def predict(self, requests):
        if not isinstance(requests, list) or not 1 <= len(requests) <= 16:
            raise ValueError("Expected 1-16 decisions")
        requests = [decision_request(request) for request in requests]
        encoded = json.dumps(requests).encode()
        if len(encoded) > 256_000:
            raise ValueError("Decision inputs exceed the request limit")
        started = time.monotonic()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(900)
            connection.connect(str(self.socket_path))
            connection.sendall(encoded + b"\n")
            with connection.makefile("rb") as source:
                result = json.loads(source.readline(32 * 1024 * 1024))
        if "error" in result:
            return {"error_code": "inference_rejected"}
        predictions = result.get("predictions", [])
        if result.get("model_identity") != self.artifact_identity or len(predictions) != len(
            requests
        ):
            raise ValueError("Decision response identity or coverage differs")
        indexed = {prediction["key"]: prediction for prediction in predictions}
        if set(indexed) != {str(index) for index in range(len(requests))}:
            raise ValueError("Decision response keys differ")
        predictions = [indexed[str(index)] for index in range(len(requests))]
        for request, prediction in zip(requests, predictions, strict=True):
            probabilities = prediction["probabilities"]
            if (
                prediction["input_sha256"] != input_digest(request)
                or prediction["kind"] != request["kind"]
                or prediction["model_identity"] != self.artifact_identity
                or len(probabilities) != len(request["options"])
                or any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities)
                or not math.isclose(sum(probabilities), 1, abs_tol=1e-6)
            ):
                raise ValueError("Invalid decision probabilities")
            prediction["options"] = request["options"]
        tokens = sum(result["input_tokens"])
        return {
            "predictions": predictions,
            "model_identity": self.artifact_identity,
            "usage": {"prompt_tokens": tokens, "completion_tokens": 0, "total_tokens": tokens},
            "latency_ms": (time.monotonic() - started) * 1000,
            "finish_reason": "stop",
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


@app.function(image=image, timeout=1200)
def pre_warm(weights_path, artifact_identity, max_model_len, gpu_type):
    journal = Journal("call:" + modal.current_function_call_id())
    journal.emit("waiting_for_worker")
    result = DecisionEndpoint.with_options(gpu=gpu_type)(
        weights_path=weights_path, artifact_identity=artifact_identity, max_model_len=max_model_len
    ).predict.remote(
        [
            {
                "state": "Serving readiness check.",
                "question": "Select an option.",
                "kind": "choice",
                "options": ["A", "B"],
            }
        ]
    )
    if result.get("error_code") or result.get("model_identity") != artifact_identity:
        journal.emit("verification_failed", error_code="verification_rejected")
        raise ValueError("Decision serving verification failed")
    journal.emit("generation_verified", completed=1, total=1, unit="decisions")
    return {"verified": True, "artifact_identity": artifact_identity}


@app.function(image=image, timeout=1200)
def inference_request(request_id, payload):
    modal.Dict.from_name(STORE, create_if_missing=True).put(
        "request:" + request_id, modal.current_function_call_id()
    )
    journal = Journal("call:" + modal.current_function_call_id())
    journal.emit("waiting_for_worker")
    started = time.monotonic()
    result = DecisionEndpoint.with_options(gpu=payload["gpu_type"])(
        weights_path=payload["weights_path"],
        artifact_identity=payload["artifact_identity"],
        max_model_len=payload["max_model_len"],
    ).predict.remote(payload["decisions"])
    result["latency_ms"] = (time.monotonic() - started) * 1000
    journal.emit(
        "failed" if result.get("error_code") else "completed",
        elapsed_seconds=time.monotonic() - started,
    )
    return result
