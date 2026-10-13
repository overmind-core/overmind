"""Boot a decision server and score one systemone request. Imported inside Modal."""

from __future__ import annotations

import hashlib
import json
import math
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = 8899
MAX_LEN = 32768

_INFERENCE_FIELDS = (
    "format_version",
    "format_id",
    "prompt",
    "base_model",
    "revision",
    "codes",
    "token_ids",
    "temperature",
    "attention_mode",
    "pooling",
    "max_length",
    "readout_dtype",
)
_TEXT_PREFIXES = ("language_model.", "model.language_model.")
_VISION_PREFIXES = ("visual.", "model.visual.")
_MANIFEST_FILES = (
    "config.json",
    "decision_config.json",
    "readout.safetensors",
    "tokenizer.json",
    "tokenizer_config.json",
    "chat_template.jinja",
    "model.safetensors",
    "preprocessor_config.json",
    "video_preprocessor_config.json",
)


def source_key(source: str) -> str:
    # Modal class parameters reject slashes.
    return source.replace("/", "--")


def source_from_key(key: str) -> str:
    if key.startswith("--"):
        return "/" + key[2:].replace("--", "/")
    return key.replace("--", "/")


def ensure_manifest(root: Path) -> None:
    """The router refuses a D3 directory whose manifest was not hashed from these files."""
    manifest_path = root / "MODEL_MANIFEST.json"
    if manifest_path.is_file():
        return
    names = [name for name in _MANIFEST_FILES if (root / name).is_file()]
    files = {name: _sha256(root / name) for name in names}
    weights = _header(root / "model.safetensors")
    counts = {"text": 0, "vision": 0}
    for name, meta in weights.items():
        if name == "__metadata__":
            continue
        group = "vision" if name.startswith(_VISION_PREFIXES) else "text"
        if not name.startswith(_TEXT_PREFIXES + _VISION_PREFIXES):
            raise RuntimeError(f"checkpoint tensor belongs to no known module: {name}")
        counts[group] += math.prod(meta["shape"])
    decision = json.loads((root / "decision_config.json").read_text())
    hidden = json.loads((root / "config.json").read_text())["text_config"]["hidden_size"]
    readout = 255 * hidden
    identity_files = {
        name: files[name]
        for name in (
            "model.safetensors",
            "readout.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "chat_template.jinja",
        )
        if name in files
    }
    fields = {key: decision[key] for key in _INFERENCE_FIELDS if key in decision}
    blob = json.dumps(
        {"files": identity_files, "decision": fields},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    manifest = {
        "schema": "d3-package-manifest/1",
        "format_id": "d3-code-readout-v1",
        "model_name": root.name,
        "licence": {"spdx": "Apache-2.0"},
        "files_sha256": files,
        "files_bytes": {name: (root / name).stat().st_size for name in names},
        "parameters": {
            "text": counts["text"],
            "vision": counts["vision"],
            "readout": readout,
            "loaded": counts["text"] + counts["vision"] + readout,
        },
        "identity": {"model_sha256": hashlib.sha256(blob).hexdigest()},
    }
    manifest_path.write_text(json.dumps(manifest))


def boot(kind: str, source: str):
    if kind == "clef":
        proc = _popen(_clef_command(source))
        wait_until_ready(proc)
        return proc
    if Path(source).is_dir():
        ensure_manifest(Path(source))
    runtime = _load_d3(source)
    _serve_d3(runtime)
    return runtime


def load_adapter(loaded: set[str], path: str) -> str:
    name = source_key(path)
    if name in loaded:
        return name
    source = Path(path)
    if not (source / "adapter_config.json").is_file():
        raise FileNotFoundError(f"LoRA adapter is missing: {source}")
    # vLLM maps the safetensors file, and Modal rejects a volume reload while that file is open.
    staged = Path("/tmp/overmind-adapters") / name
    if staged.exists():
        shutil.rmtree(staged)
    staged.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, staged)
    status, text = _post("/v1/load_lora_adapter", {"lora_name": name, "lora_path": str(staged)})
    if status == 200 or "already been loaded" in text:
        loaded.add(name)
        print(f"adapter {name} ready from {staged}", flush=True)
        return name
    raise RuntimeError(f"loading adapter {name} failed: {status} {text[:300]}")


def wait_until_ready(proc: subprocess.Popen, timeout: int = 45 * 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"decision server exited {proc.returncode}: {_tail(proc)}")
        if _healthy():
            return
        time.sleep(2)
    raise RuntimeError(f"decision server was not ready: {_tail(proc)}")


def score(source: str, payload: dict) -> dict:
    body = json.dumps(
        {
            "model": source,
            "state": payload["state"],
            "questions": payload["questions"],
        }
    ).encode()
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}/v1/systemone",
        data=body,
        headers={"content-type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10 * 60) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode()[:500]
        if "max" in detail and ("length" in detail or "context" in detail):
            return {"error": "max_length_exceeded"}
        return {"error": detail or f"HTTP {exc.code}"}


def _popen(command: list[str]) -> subprocess.Popen:
    print("Starting", " ".join(command), flush=True)
    proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)  # noqa: S603
    _capture(proc)
    return proc


def _post(path: str, payload: dict) -> tuple[int, str]:
    request = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10 * 60) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()


def _load_d3(source: str):
    from vllm_srun.cli import build_parser, config_from_args
    from vllm_srun.runtime import Runtime

    args = build_parser().parse_args(
        ["serve", source, "--served-model-name", source, "--device", "cuda", "--port", str(PORT)]
    )
    runtime = Runtime(config_from_args(args))
    runtime.start(background=False)
    if not runtime.health.ready:
        raise RuntimeError("D3 runtime did not become ready")
    return runtime


def _serve_d3(runtime) -> None:
    if _port_open():
        return
    import uvicorn
    from vllm_srun.api.app import create_app

    server = uvicorn.Server(
        uvicorn.Config(
            create_app(runtime),
            host="127.0.0.1",
            port=PORT,
            log_level="info",
            access_log=False,
            lifespan="off",
        )
    )
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 60
    while time.time() < deadline:
        if _port_open():
            return
        time.sleep(0.2)
    raise RuntimeError("D3 server did not bind")


def _clef_command(source: str) -> list[str]:
    return [
        "vllm",
        "serve",
        source,
        "--served-model-name",
        source,
        "--max-model-len",
        str(MAX_LEN),
        "--logprobs-mode",
        "raw_logprobs",
        "--enforce-eager",
        "--gpu-memory-utilization",
        "0.9",
        "--enable-lora",
        "--max-loras",
        "8",
        "--max-lora-rank",
        "64",
        "--max-cpu-loras",
        "32",
        "--port",
        str(PORT),
    ]


def _capture(proc: subprocess.Popen) -> None:
    def _read() -> None:
        assert proc.stdout is not None
        lines: list[str] = []
        for line in proc.stdout:
            lines.append(line)
            del lines[:-40]
            proc._serve_log = "".join(lines)  # type: ignore[attr-defined]

    threading.Thread(target=_read, daemon=True).start()


def _tail(proc: subprocess.Popen) -> str:
    return getattr(proc, "_serve_log", "")[-1500:]


def _port_open() -> bool:
    try:
        with socket.create_connection(("127.0.0.1", PORT), timeout=1):
            return True
    except OSError:
        return False


def _healthy() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/health", timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _header(path: Path) -> dict:
    import struct

    with path.open("rb") as handle:
        size = struct.unpack("<Q", handle.read(8))[0]
        return json.loads(handle.read(size))
