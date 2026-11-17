"""SageMaker dedicated serving. The container fetches the checkpoint itself.

SageMaker copies an ArtifactUrl onto the instance as files and does not unzip
checkpoint.zip. LoRA also needs the base snapshot; a full finetune is the zip alone.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import traceback
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from modal_shared.serving.args import VllmServeContext, build_vllm_args, snap_lora_rank
from modal_shared.serving.decision_http import decision_base_vllm_command, decision_vllm_command
from overbae.sagemaker.s3_io import download_file, sync_s3_to_directory

VLLM_PORT = 8000
SAGEMAKER_PORT = 8080
WORK = Path("/opt/ml/model")


def parse_s3_uri(uri: str) -> tuple[str, str]:
    rest = uri.removeprefix("s3://")
    bucket, _, key = rest.partition("/")
    if not bucket or not key:
        raise ValueError(f"Expected s3://bucket/key, got {uri!r}")
    return bucket, key


def unzip_checkpoint(zip_path: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            parts = Path(info.filename).parts
            if not parts or info.filename.startswith("/") or ".." in parts:
                continue
            archive.extract(info, dest)


def stage_weights(root: Path) -> tuple[Path, Path | None]:
    """Return ``(model_dir, adapter_dir)``. ``adapter_dir`` is set for LoRA."""
    bucket, key = parse_s3_uri(os.environ["OVERMIND_CHECKPOINT_S3"])
    zip_path = root / "checkpoint.zip"
    download_file(bucket, key, zip_path)
    checkpoint = root / "checkpoint"
    unzip_checkpoint(zip_path, checkpoint)

    weights_bucket = os.environ.get("OVERMIND_WEIGHTS_BUCKET", "")
    base_prefix = os.environ.get("OVERMIND_BASE_PREFIX", "")
    if not weights_bucket or not base_prefix:
        return checkpoint, None
    base = root / "base"
    sync_s3_to_directory(weights_bucket, base_prefix, base)
    return base, checkpoint


def _local_vllm(argv: list[str], gpu_type: str) -> list[str]:
    """Weights are on instance disk, and A10 has no FP8."""
    cleaned: list[str] = []
    index = 0
    while index < len(argv):
        flag = argv[index]
        if flag in {"--load-format", "--model-loader-extra-config"}:
            index += 2
            continue
        cleaned.append(flag)
        index += 1
    if gpu_type.startswith("A10") and "--kv-cache-dtype" in cleaned:
        cleaned[cleaned.index("--kv-cache-dtype") + 1] = "bfloat16"
    return cleaned


def serve_argv(
    model_dir: Path,
    adapter_dir: Path | None,
    *,
    contract: str,
    served_name: str,
    base_model: str,
    max_model_len: int,
    gpu_type: str,
) -> list[str]:
    if contract == "decision" and adapter_dir is None:
        return _local_vllm(
            decision_base_vllm_command(
                model_path=str(model_dir),
                served_name=served_name,
                max_model_len=max_model_len,
                port=VLLM_PORT,
            ),
            gpu_type,
        )
    if contract == "decision":
        return _local_vllm(
            decision_vllm_command(
                model_path=str(model_dir),
                served_name=served_name,
                adapter_path=str(adapter_dir),
                max_lora_rank=0,
                max_model_len=max_model_len,
                port=VLLM_PORT,
            ),
            gpu_type,
        )
    adapters: tuple[tuple[str, str], ...] = ()
    rank = 16
    if adapter_dir is not None:
        config = adapter_dir / "adapter_config.json"
        if config.is_file():
            rank = int(json.loads(config.read_text()).get("r") or 16)
        adapters = ((served_name, str(adapter_dir)),)
    return _local_vllm(
        build_vllm_args(
            VllmServeContext(
                model_path=str(model_dir),
                model_name=served_name,
                base_model=base_model,
                max_model_len=max_model_len,
                port=VLLM_PORT,
                gpu_type=gpu_type,
                max_lora_rank=snap_lora_rank(rank),
                lora_adapters=adapters,
                quantization="bf16",
            )
        ),
        gpu_type,
    )


def _healthy() -> bool:
    try:
        with urlopen(f"http://127.0.0.1:{VLLM_PORT}/health", timeout=1) as resp:
            return resp.status == 200
    except (URLError, TimeoutError, OSError):
        return False


def _proxy(contract: str) -> None:
    upstream = "/v1/systemone" if contract == "decision" else "/v1/chat/completions"

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/ping":
                self.send_error(404)
                return
            ok = _healthy()
            body = b"ok" if ok else b"loading"
            self.send_response(200 if ok else 503)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/invocations":
                self.send_error(404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            payload = self.rfile.read(length)
            request = Request(
                f"http://127.0.0.1:{VLLM_PORT}{upstream}",
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=300) as resp:
                    raw = resp.read()
                    status = resp.status
                    content_type = resp.headers.get("Content-Type", "application/json")
            except URLError as exc:
                raw = json.dumps({"error": str(exc)}).encode()
                status = 503
                content_type = "application/json"
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("%s\n" % (fmt % args))

    ThreadingHTTPServer(("0.0.0.0", SAGEMAKER_PORT), Handler).serve_forever()


def _retarget_adapter(adapter_dir: Path, base_dir: Path) -> None:
    """The checkpoint names the Hub repo. Load the snapshot already on disk."""
    adapter_config = adapter_dir / "adapter_config.json"
    if adapter_config.is_file():
        config = json.loads(adapter_config.read_text())
        config["base_model_name_or_path"] = str(base_dir)
        adapter_config.write_text(json.dumps(config))
    decision_config = adapter_dir / "unsloth_decision_config.json"
    if decision_config.is_file():
        config = json.loads(decision_config.read_text())
        config["base_model"] = str(base_dir)
        decision_config.write_text(json.dumps(config))


def serve_decision() -> int:
    """Clef answers in-process. The vLLM image has no /v1/systemone route."""
    root = WORK
    root.mkdir(parents=True, exist_ok=True)
    holder: dict = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/ping":
                self.send_error(404)
                return
            body = b"ok" if holder.get("ready") else b"loading"
            self.send_response(200 if holder.get("ready") else 503)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            if self.path.split("?", 1)[0] != "/invocations" or not holder.get("ready"):
                self.send_error(503 if self.path.split("?", 1)[0] == "/invocations" else 404)
                return
            length = int(self.headers.get("Content-Length") or 0)
            payload = json.loads(self.rfile.read(length) or b"{}")
            try:
                result = holder["decide"](payload["state"], payload["questions"])
                raw = json.dumps(result).encode()
                status = 200
            except Exception as exc:  # noqa: BLE001
                raw = json.dumps({"error": str(exc)}).encode()
                status = 400
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, fmt: str, *args) -> None:
            sys.stderr.write("%s\n" % (fmt % args))

    server = ThreadingHTTPServer(("0.0.0.0", SAGEMAKER_PORT), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        model_dir, adapter_dir = stage_weights(root)
        folder = adapter_dir or model_dir
        if adapter_dir is not None:
            _retarget_adapter(adapter_dir, model_dir)
        max_model_len = int(os.environ.get("OVERMIND_MAX_MODEL_LEN", "2048"))
        from unsloth import FastDecisionModel
        from unsloth.models.decision import _clef_decide

        model, processor = FastDecisionModel.from_pretrained(
            str(folder),
            max_seq_length=max_model_len,
            local_files_only=True,
            use_gradient_checkpointing=False,
        )
        FastDecisionModel.for_inference(model)
        tokenizer = getattr(processor, "tokenizer", processor)

        def decide(state, questions):
            return _clef_decide(model, tokenizer, state, questions, max_length=max_model_len)

        holder["decide"] = decide
        holder["ready"] = True
        print("decision-ready", flush=True)
        threading.Event().wait()
    except Exception:
        traceback.print_exc()
        time.sleep(20)
        return 1
    return 0


def main() -> int:
    if os.environ.get("OVERMIND_CONTRACT", "chat") == "decision":
        return serve_decision()
    root = WORK
    root.mkdir(parents=True, exist_ok=True)
    proxy = threading.Thread(
        target=_proxy,
        args=(os.environ.get("OVERMIND_CONTRACT", "chat"),),
        daemon=True,
    )
    proxy.start()
    try:
        model_dir, adapter_dir = stage_weights(root)
        argv = serve_argv(
            model_dir,
            adapter_dir,
            contract=os.environ.get("OVERMIND_CONTRACT", "chat"),
            served_name=os.environ.get("OVERMIND_SERVED_NAME", "model"),
            base_model=os.environ.get("OVERMIND_BASE_MODEL", ""),
            max_model_len=int(os.environ.get("OVERMIND_MAX_MODEL_LEN", "2048")),
            gpu_type=os.environ.get("OVERMIND_GPU_TYPE", "H100"),
        )
        print(" ".join(argv), flush=True)
        process = subprocess.Popen(argv)  # noqa: S603
    except Exception:
        traceback.print_exc()
        time.sleep(20)
        return 1
    code = process.wait()
    if code:
        # SageMaker drops stdout when the process exits immediately.
        print(f"vllm-exit {code}", flush=True)
        time.sleep(20)
    return code


if __name__ == "__main__":
    sys.exit(main())
