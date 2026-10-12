"""H100 decision-model worker. Chat SFT stays on modal_sft_worker."""

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
        "pip install --upgrade 'unsloth[base] @ git+https://github.com/unslothai/unsloth.git' "
        "'unsloth_zoo[base] @ git+https://github.com/unslothai/unsloth-zoo.git'",
        "pip install --upgrade --no-deps 'tokenizers>=0.23.1,<0.24' 'trl==0.22.2'",
        "pip install 'transformers==5.17.0'",
        "pip install --no-deps 'torchao==0.16.0'",
    )
    .env({"TOKENIZERS_PARALLELISM": "false", "PYTHONPATH": "/root", "HF_HOME": "/tmp/hf"})
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
    .add_local_file(Path(__file__).with_name("decision_serve.py"), "/root/decision_serve.py")
)
d3_image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("git")
    .pip_install("torch==2.8.0", index_url="https://download.pytorch.org/whl/cu128")
    .pip_install("safetensors", "huggingface_hub", "numpy")
    .pip_install(
        "vllm-srun[multimodal] @ git+https://github.com/vllm-project/semantic-router.git#subdirectory=src/model-runtime"
    )
    .env(_serve_env)
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


@app.function(
    image=image,
    gpu="H100",
    timeout=24 * 60 * 60,
    secrets=[secret],
    volumes={"/artifacts": volume},
)
def train_decision(payload: dict) -> dict:
    import decision_train

    result = decision_train.train(payload)
    volume.commit()
    return result


class _Server:
    """Never raise from enter: Modal retries enter failures forever."""

    kind = ""

    @modal.enter()
    def startup(self) -> None:
        import decision_serve

        self._startup_error = None
        self._loaded: set[str] = set()
        try:
            source = decision_serve.source_from_key(self.source)
            if source.startswith("/artifacts"):
                volume.reload()
            decision_serve.boot(self.kind, source)
        except Exception as exc:
            self._startup_error = f"{type(exc).__name__}: {exc}"
            print(self._startup_error, flush=True)

    @modal.method()
    def score(self, payload: dict) -> dict:
        import decision_serve

        if self._startup_error:
            return {"error": self._startup_error}
        source = decision_serve.source_from_key(self.source)
        adapter = payload.get("adapter")
        try:
            if adapter:
                volume.reload()
                source = decision_serve.load_adapter(self._loaded, adapter)
        except Exception as exc:
            return {"error": f"{type(exc).__name__}: {exc}"}
        return decision_serve.score(source, payload)


@app.cls(image=clef_image, **_RUN)
class ClefServer(_Server):
    source: str = modal.parameter()
    kind = "clef"


@app.cls(image=d3_image, **_RUN)
class D3Server(_Server):
    source: str = modal.parameter()
    kind = "d3"
