"""H100 decision-model worker. Chat SFT stays on modal_sft_worker."""

from __future__ import annotations

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


@app.function(
    image=image,
    gpu="H100",
    timeout=2 * 60 * 60,
    secrets=[secret],
    scaledown_window=300,
)
def serve_decision(payload: dict) -> dict:
    import decision_train

    return decision_train.predict(payload)
