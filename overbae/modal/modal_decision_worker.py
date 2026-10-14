"""H100 decision-model worker. Chat SFT stays on modal_sft_worker.

Volume layout: ``/artifacts/.bases/<org>--<repo>`` is the shared base snapshot.
``/artifacts/<job_id>/records.jsonl`` and ``eval.jsonl`` are the trainer records.
Trainers write under a subfolder of ``/artifacts/<job_id>/`` and never remove the job folder.
"""

import json
from pathlib import Path

import modal

APP_NAME = "overmind-decision"
VOLUME_NAME = "overmind-decision"

app = modal.App(APP_NAME)
volume = modal.Volume.from_name(VOLUME_NAME, create_if_missing=True)
secret = modal.Secret.from_name("overmind-inference")
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git", "build-essential")
    .pip_install(
        "torch==2.8.0",
        "torchvision==0.23.0",
        "torchaudio==2.8.0",
        index_url="https://download.pytorch.org/whl/cu128",
    )
    .pip_install("xformers==0.0.32.post2", index_url="https://download.pytorch.org/whl/cu128")
    .pip_install(
        "bitsandbytes",
        "datasets",
        "accelerate",
        "peft",
        "huggingface_hub",
        "safetensors",
    )
    .run_commands(
        # Pinned: FastDecisionModel and the gold format it reads live on these commits.
        "pip install --upgrade 'unsloth[base] @ git+https://github.com/unslothai/unsloth.git"
        "@dd5ec4877ee08cfdf169990b52854b2986ed3c5a' "
        "'unsloth_zoo[base] @ git+https://github.com/unslothai/unsloth-zoo.git"
        "@a81d3310e36fe30cf590622ba0be0426166ac2a6'",
        "pip install --upgrade --no-deps 'tokenizers>=0.23.1,<0.24' 'trl==0.22.2'",
        "pip install 'transformers==5.17.0'",
        "pip install --no-deps 'torchao==0.16.0'",
    )
    .env({"TOKENIZERS_PARALLELISM": "false", "PYTHONPATH": "/root", "HF_HOME": "/tmp/hf"})
    .add_local_file(Path(__file__).with_name("decision_cache.py"), "/root/decision_cache.py")
    .add_local_file(Path(__file__).with_name("decision_train.py"), "/root/decision_train.py")
)
# Nightly wheel: structured decisions are on vLLM main, not the stable release.
_VLLM_WHEEL = (
    "https://wheels.vllm.ai/45bb710ecaa2f0e100d1cab27308186e9f4aa7dd/"
    "vllm-0.31.1rc1.dev342%2Bg45bb710ec-cp38-abi3-manylinux_2_28_x86_64.whl"
)
_serve_env = {"TOKENIZERS_PARALLELISM": "false", "PYTHONPATH": "/root", "HF_HOME": "/tmp/hf"}
clef_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(_VLLM_WHEEL)
    # vLLM reads this at process start. Setting it after boot does not enable adapter loads.
    .env({**_serve_env, "VLLM_ALLOW_RUNTIME_LORA_UPDATING": "1"})
    .add_local_file(Path(__file__).with_name("decision_cache.py"), "/root/decision_cache.py")
    .add_local_file(Path(__file__).with_name("decision_serve.py"), "/root/decision_serve.py")
)
d1_train_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.8.0",
        "torchvision==0.23.0",
        index_url="https://download.pytorch.org/whl/cu128",
    )
    .pip_install(
        "transformers==5.17.0",
        "peft==0.21.2",
        "accelerate",
        "huggingface_hub",
        "safetensors",
        "pillow",
        "numpy",
    )
    .env({"TOKENIZERS_PARALLELISM": "false", "PYTHONPATH": "/root", "HF_HOME": "/tmp/hf"})
    .add_local_file(Path(__file__).with_name("decision_cache.py"), "/root/decision_cache.py")
    .add_local_file(Path(__file__).with_name("decision_train.py"), "/root/decision_train.py")
)
d1_serve_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install(
        "torch==2.8.0",
        "torchvision==0.23.0",
        index_url="https://download.pytorch.org/whl/cu128",
    )
    .pip_install(
        "transformers==5.17.0",
        "huggingface_hub",
        "safetensors",
        "pillow",
        "numpy",
    )
    .env(_serve_env)
    .add_local_file(Path(__file__).with_name("decision_cache.py"), "/root/decision_cache.py")
    .add_local_file(Path(__file__).with_name("decision_serve.py"), "/root/decision_serve.py")
)
d3_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .pip_install("torch==2.8.0", index_url="https://download.pytorch.org/whl/cu128")
    .pip_install("safetensors", "huggingface_hub", "numpy")
    .pip_install(
        "vllm-srun[multimodal] @ git+https://github.com/vllm-project/semantic-router.git"
        "@4d15138290983e727bb55c9a331d66c765b6f20d#subdirectory=src/model-runtime"
    )
    .env(_serve_env)
    .add_local_file(Path(__file__).with_name("decision_cache.py"), "/root/decision_cache.py")
    .add_local_file(Path(__file__).with_name("decision_serve.py"), "/root/decision_serve.py")
)
# One container per base (Clef) or per merged checkpoint (D3). Idle workers scale to zero.
_RUN = {
    "gpu": "H100",
    "cpu": 8,
    "memory": 131072,
    "timeout": 2 * 60 * 60,
    "scaledown_window": 300,
    "secrets": [secret],
    "volumes": {"/artifacts": volume},
    "retries": modal.Retries(max_retries=0),
}


_light = modal.Image.debian_slim(python_version="3.11")
_cache_image = _light.pip_install("huggingface_hub").add_local_file(
    Path(__file__).with_name("decision_cache.py"), "/root/decision_cache.py"
)


def _read(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _error(code: str, message: str) -> dict:
    return {"error": {"code": code, "message": message[:500]}}


def _stage_base(repo: str) -> None:
    if repo.startswith("/"):
        return
    cache_base.remote(repo)
    volume.reload()


@app.function(
    image=_cache_image,
    timeout=4 * 60 * 60,
    max_containers=1,
    volumes={"/artifacts": volume},
    secrets=[secret],
    cpu=8,
)
@modal.concurrent(max_inputs=1)
def cache_base(repo: str) -> dict:
    import decision_cache

    volume.reload()
    folder, fetched = decision_cache.ensure(repo)
    if fetched:
        volume.commit()
    return {"path": str(folder), "fetched": fetched}


@app.function(image=_light, timeout=15 * 60, volumes={"/artifacts": volume})
def upload_records(job_id: str, records_jsonl: str, eval_jsonl: str | None = None) -> dict:
    folder = Path("/artifacts") / job_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "records.jsonl").write_text(records_jsonl)
    if eval_jsonl is not None:
        (folder / "eval.jsonl").write_text(eval_jsonl)
    volume.commit()
    return {"records": records_jsonl.count("\n"), "eval": (eval_jsonl or "").count("\n")}


@app.function(
    image=image,
    gpu="H100",
    timeout=24 * 60 * 60,
    secrets=[secret],
    volumes={"/artifacts": volume},
)
def train_decision(payload: dict) -> dict:
    import decision_train

    volume.reload()
    try:
        _stage_base(payload["repo"])
    except Exception as exc:
        return _error("base_unavailable", f"{type(exc).__name__}: {exc}")
    records = _read(Path("/artifacts") / payload["job_id"] / "records.jsonl")
    try:
        result = decision_train.train({**payload, "records": records})
    except Exception as exc:
        return _error("runtime_error", f"{type(exc).__name__}: {exc}")
    if "error" not in result:
        volume.commit()
    return result


@app.function(
    image=d1_train_image,
    gpu="H100",
    timeout=24 * 60 * 60,
    secrets=[secret],
    volumes={"/artifacts": volume},
)
def train_d1(payload: dict) -> dict:
    import decision_train

    volume.reload()
    try:
        _stage_base(payload["repo"])
    except Exception as exc:
        return _error("base_unavailable", f"{type(exc).__name__}: {exc}")
    records = _read(Path("/artifacts") / payload["job_id"] / "records.jsonl")
    try:
        result = decision_train.train({**payload, "records": records})
    except Exception as exc:
        return _error("runtime_error", f"{type(exc).__name__}: {exc}")
    if "error" not in result:
        volume.commit()
    return result


@app.function(image=_light, timeout=6 * 60 * 60, volumes={"/artifacts": volume})
def evaluate_decision(payload: dict) -> dict:
    """Answers per evaluation row from the base model and the trained one, in row order."""
    volume.reload()
    records = _read(Path("/artifacts") / payload["job_id"] / "eval.jsonl")
    server = {"clef": ClefServer, "d3": D3Server, "d1": D1Server}[payload["family"]]
    requests = [
        {
            "state": record["state"],
            "questions": record["questions"],
            "max_context_length": payload.get("max_context_length"),
        }
        for record in records
    ]
    base = server(source=payload["repo"].replace("/", "--"))
    if payload["family"] == "clef":
        trained = base
        trained_requests = [{**request, "adapter": payload["checkpoint"]} for request in requests]
    else:
        trained = server(source=payload["checkpoint"].replace("/", "--"))
        trained_requests = requests
    return {
        "base": base.score_many.remote(requests),
        "trained": trained.score_many.remote(trained_requests),
    }


class _Server:
    """Never raise from enter: Modal retries enter failures forever."""

    kind = ""

    @modal.enter()
    def startup(self) -> None:
        import decision_serve

        self._startup_error = None
        self._loaded: set[str] = set()
        try:
            volume.reload()
            source = decision_serve.source_from_key(self.source)
            _stage_base(source)
            decision_serve.boot(self.kind, source)
        except Exception as exc:
            self._startup_error = f"{type(exc).__name__}: {exc}"
            print(self._startup_error, flush=True)

    @modal.method()
    def score(self, payload: dict) -> dict:
        import decision_serve

        if self._startup_error:
            return _error("boot_failed", self._startup_error)
        source = decision_serve.source_from_key(self.source)
        adapter = payload.get("adapter")
        try:
            if adapter:
                volume.reload()
                source = decision_serve.load_adapter(self._loaded, adapter)
        except FileNotFoundError as exc:
            return _error("adapter_missing", str(exc))
        except Exception as exc:
            return _error("runtime_error", f"{type(exc).__name__}: {exc}")
        return decision_serve.score(source, payload)

    @modal.method()
    def score_many(self, payloads: list[dict]) -> list[dict]:
        """One result per payload, in order: ``answers`` on success, ``error`` otherwise."""
        return [self.score.local(payload) for payload in payloads]


@app.cls(image=clef_image, **_RUN)
class ClefServer(_Server):
    source: str = modal.parameter()
    kind = "clef"


@app.cls(image=d3_image, **_RUN)
class D3Server(_Server):
    source: str = modal.parameter()
    kind = "d3"


@app.cls(image=d1_serve_image, **{**_RUN, "gpu": "L4", "memory": 65536})
class D1Server(_Server):
    source: str = modal.parameter()
    kind = "d1"
