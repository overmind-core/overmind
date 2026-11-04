import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import modal

from modal_shared.decision_artifact import read_artifact
from modal_shared.decisions import decision_request
from modal_shared.images.serve import dedicated_vllm_image
from modal_shared.images.train import TRAIN_IMAGES
from modal_shared.operational_events import STORE, Journal
from modal_shared.serving.artifacts import read_base_manifest
from modal_shared.serving.decision_http import (
    decision_base_vllm_command,
    decision_vllm_command,
    probabilities_for,
    systemone_body,
)
from modal_shared.serving.stage import stage_base
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


VLLM_PORT = 8000


def _wait_until_healthy(process: subprocess.Popen, log_path: Path) -> None:
    deadline = time.monotonic() + 900
    url = f"http://127.0.0.1:{VLLM_PORT}/health"
    while time.monotonic() < deadline:
        if process.poll() is not None:
            tail = log_path.read_text(errors="replace")[-2000:] if log_path.is_file() else ""
            raise RuntimeError(f"vLLM exited before becoming healthy: {tail}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError):
            time.sleep(1)
    raise RuntimeError("vLLM did not become healthy")


def _decision_worker(cls_name):
    class DecisionEndpoint:
        max_model_len: int = modal.parameter()
        weights_path: str = modal.parameter(default="")
        artifact_identity: str = modal.parameter(default="")
        base_repo: str = modal.parameter(default="")

        @modal.enter()
        def load(self):
            self.work = Path(tempfile.mkdtemp(prefix="decision-serving-"))
            log_path = self.work / "worker.log"
            self.log = log_path.open("w")
            if self.base_repo:
                if self.base_repo == "LiquidAI/d1-omni-600M":
                    raise ValueError(
                        "LiquidAI/d1-omni-600M cannot be served: text_config has no num_attention_heads."
                    )
                self.served_name = self.base_repo
                self.identity = self.base_repo
                command = decision_base_vllm_command(
                    model_path=self.base_repo,
                    served_name=self.served_name,
                    max_model_len=self.max_model_len,
                    port=VLLM_PORT,
                )
            else:
                weights.reload()
                checkpoint = artifact_path(self.artifact_identity)
                if str(checkpoint) != self.weights_path:
                    raise ValueError("Serving path differs from the pinned artifact")
                artifact = read_artifact(checkpoint)
                if artifact["identity"] != self.artifact_identity:
                    raise ValueError("Serving identity differs")
                config = artifact["training"]["model_config"]
                base_path = Path(config["BASE_MODEL_PATH"])
                base = read_base_manifest(base_path)
                if base["identity"] != artifact["training"]["base_identity"]:
                    raise ValueError("Serving base identity differs")
                if config["UNSLOTH_IMAGE"] != TRAIN_DECISION:
                    raise ValueError("Decision artifact requires another runtime")
                if not (checkpoint / "joint_head.safetensors").is_file():
                    raise ValueError("Decision artifact is missing its trained head")
                staged = stage_base(base_path, checkpoint, self.work / "serve")
                for name in (
                    "joint_head.safetensors",
                    "joint_head_config.json",
                    "unsloth_decision_config.json",
                ):
                    src = checkpoint / name
                    if not src.is_file():
                        continue
                    dest = staged / name
                    if dest.is_symlink() or dest.exists():
                        dest.unlink()
                    dest.symlink_to(src)
                self.served_name = "decision"
                self.identity = self.artifact_identity
                command = decision_vllm_command(
                    model_path=str(staged),
                    served_name=self.served_name,
                    adapter_path=str(checkpoint),
                    max_lora_rank=0,
                    max_model_len=self.max_model_len,
                    port=VLLM_PORT,
                )
            self.process = subprocess.Popen(
                command,
                cwd=self.work,
                stdout=self.log,
                stderr=subprocess.STDOUT,
            )
            _wait_until_healthy(self.process, log_path)

        def _ask(self, request: dict) -> tuple[list[float], dict]:
            body = json.dumps(systemone_body(request, self.served_name)).encode()
            request_http = urllib.request.Request(
                f"http://127.0.0.1:{VLLM_PORT}/v1/systemone",
                data=body,
                headers={"content-type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(request_http, timeout=120) as response:
                    payload = json.loads(response.read())
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:500]
                raise ValueError(detail or "vLLM rejected the decision") from exc
            answer = (payload.get("answers") or {}).get("0") or {}
            return probabilities_for(request, answer), payload.get("usage") or {}

        @modal.method()
        def predict(self, requests):
            if not isinstance(requests, list) or not 1 <= len(requests) <= 16:
                raise ValueError("Expected 1-16 decisions")
            requests = [decision_request(request) for request in requests]
            if len(json.dumps(requests).encode()) > 256_000:
                raise ValueError("Decision inputs exceed the request limit")
            started = time.monotonic()
            predictions = []
            prompt_tokens = 0
            try:
                for index, request in enumerate(requests):
                    probabilities, usage = self._ask(request)
                    prompt_tokens += int(
                        usage.get("input_tokens") or usage.get("prompt_tokens") or 0
                    )
                    predictions.append(
                        {
                            "key": str(index),
                            "kind": request["kind"],
                            "options": request["options"],
                            "probabilities": probabilities,
                            "model_identity": self.identity,
                        }
                    )
            except ValueError:
                return {"error_code": "inference_rejected"}
            return {
                "predictions": predictions,
                "model_identity": self.identity,
                "usage": {
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": 0,
                    "total_tokens": prompt_tokens,
                },
                "latency_ms": (time.monotonic() - started) * 1000,
                "finish_reason": "stop",
            }

        @modal.exit()
        def stop(self):
            if hasattr(self, "process"):
                self.process.terminate()
                try:
                    self.process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    self.process.kill()
            if hasattr(self, "log"):
                self.log.close()
            if hasattr(self, "work"):
                shutil.rmtree(self.work, ignore_errors=True)

    DecisionEndpoint.__name__ = cls_name
    DecisionEndpoint.__qualname__ = cls_name
    return DecisionEndpoint


def register_decision_endpoint(modal_app, cls_name, *, max_containers=1):
    # The serving-release secret is what lets a container import build this app object.
    return modal_app.cls(
        image=dedicated_vllm_image,
        gpu="H100",
        timeout=1800,
        scaledown_window=120,
        max_containers=max_containers,
        volumes={"/weights": weights},
        secrets=[modal.Secret.from_dict({"OVERMIND_SERVING_RELEASE": json.dumps(RELEASE)})],
    )(modal.concurrent(max_inputs=16)(_decision_worker(cls_name)))


DecisionEndpoint = register_decision_endpoint(app, "DecisionEndpoint")
# Modal loads a deployed class with getattr(module, name). This is the user class.
decision_dedicated = _decision_worker("decision_dedicated")


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
