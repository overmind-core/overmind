"""vLLM /v1/systemone argv and the predict envelope DecisionEndpoint returns."""

from __future__ import annotations

import json
import math
from pathlib import Path

from modal_shared.serving.args import snap_lora_rank


def decision_vllm_command(
    *,
    model_path: str,
    served_name: str,
    adapter_path: str,
    max_lora_rank: int,
    max_model_len: int,
    port: int = 8000,
) -> list[str]:
    """One joint head overlaid on the base, then that artifact's LoRA."""
    rank = int(max_lora_rank or 0)
    config = Path(adapter_path) / "adapter_config.json"
    if config.is_file():
        stored = int(json.loads(config.read_text()).get("r") or 0)
        rank = max(rank, stored)
    rank = snap_lora_rank(rank or 16)
    return [
        "vllm",
        "serve",
        model_path,
        "--served-model-name",
        f"{served_name}--base",
        "--enable-lora",
        "--max-loras",
        "1",
        "--max-lora-rank",
        str(rank),
        "--lora-modules",
        f"{served_name}={adapter_path}",
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--max-model-len",
        str(max_model_len),
        "--gpu-memory-utilization",
        "0.90",
        "--enforce-eager",
        "--trust-remote-code",
        "--dtype",
        "bfloat16",
    ]


def decision_base_vllm_command(
    *, model_path: str, served_name: str, max_model_len: int, port: int = 8000
) -> list[str]:
    """A catalog base whose decision head is already in the checkpoint."""
    return [
        "vllm",
        "serve",
        model_path,
        "--served-model-name",
        served_name,
        "--host",
        "0.0.0.0",
        "--port",
        str(port),
        "--max-model-len",
        str(max_model_len),
        "--gpu-memory-utilization",
        "0.90",
        "--enforce-eager",
        "--trust-remote-code",
        "--dtype",
        "bfloat16",
    ]


def systemone_question(request: dict) -> dict:
    kind = request["kind"]
    if kind == "noul":
        return {"type": "noul", "instructions": request["question"]}
    if kind == "score":
        return {
            "type": "score",
            "instructions": request["question"],
            "criteria": list(request["options"]),
        }
    return {
        "type": "choice",
        "instructions": request["question"],
        "criteria": {option: option for option in request["options"]},
    }


def probabilities_for(request: dict, answer: dict) -> list[float]:
    options = request["options"]
    raw = answer.get("probabilities")
    if isinstance(raw, dict):
        values = [float(raw.get(option) or 0) for option in options]
    elif isinstance(raw, list) and len(raw) == len(options):
        values = [float(item) for item in raw]
    elif request["kind"] == "noul" and "noul" in answer:
        # Wire order matches the socket worker: options[1] is the positive class.
        positive = float(answer["noul"])
        values = [1 - positive, positive]
    else:
        raise ValueError("Invalid decision probabilities")
    if len(values) != len(options) or any(
        not math.isfinite(value) or not 0 <= value <= 1 for value in values
    ):
        raise ValueError("Invalid decision probabilities")
    if not math.isclose(sum(values), 1, abs_tol=1e-3):
        raise ValueError("Invalid decision probabilities")
    return values


def systemone_body(request: dict, served_name: str) -> dict:
    return {
        "model": served_name,
        "state": request["state"],
        "questions": {"0": systemone_question(request)},
    }
