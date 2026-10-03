import os
import random
import uuid
from pathlib import Path

import torch

from modal_shared.serving.artifacts import read_base_manifest


def training_base_identity(path, repository):
    manifest = read_base_manifest(Path(path))
    if manifest["repo"] != repository:
        raise ValueError("The training base repository differs from its sealed identity")
    return manifest["identity"]


def save_resume(path, model, optimizer, scheduler, *, signature, step, tokens_seen):
    path = Path(path)
    payload = {
        "signature": signature,
        "parameters": {
            name: parameter.detach().cpu()
            for name, parameter in model.named_parameters()
            if parameter.requires_grad
        },
        "optimizer": optimizer.state_dict(),
        "scheduler": scheduler.state_dict(),
        "rng": torch.get_rng_state(),
        "python_rng": random.getstate(),
        "cuda_rng": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
        "step": step,
        "tokens_seen": tokens_seen,
    }
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            torch.save(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def restore_resume(path, model, optimizer, scheduler, *, signature):
    saved = torch.load(path, map_location="cpu", weights_only=True)
    if saved["signature"] != signature:
        raise ValueError("The checkpoint belongs to a different base/data/configuration identity")
    parameters = {name: p for name, p in model.named_parameters() if p.requires_grad}
    if parameters.keys() != saved["parameters"].keys():
        raise ValueError("Checkpoint trainable parameter coverage differs")
    for name, parameter in parameters.items():
        value = saved["parameters"][name]
        if (
            not isinstance(value, torch.Tensor)
            or parameter.shape != value.shape
            or parameter.dtype != value.dtype
        ):
            raise ValueError(f"Checkpoint parameter shape/dtype differs: {name}")
    if len(saved["cuda_rng"]) != torch.cuda.device_count():
        raise ValueError("Checkpoint CUDA device count differs")
    step, tokens_seen = saved["step"], saved["tokens_seen"]
    if type(step) is not int or step < 0 or type(tokens_seen) is not int or tokens_seen < 0:
        raise ValueError("Checkpoint progress is invalid")
    optimizer.load_state_dict(saved["optimizer"])
    scheduler.load_state_dict(saved["scheduler"])
    with torch.no_grad():
        for name, parameter in parameters.items():
            parameter.copy_(saved["parameters"][name])
    torch.set_rng_state(saved["rng"])
    random.setstate(saved["python_rng"])
    if saved["cuda_rng"]:
        torch.cuda.set_rng_state_all(saved["cuda_rng"])
    return step, tokens_seen
