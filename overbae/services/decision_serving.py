import json
import os
from pathlib import Path

import modal

from modal_shared.decisions import decision_request
from modal_shared.training_release import serving_identity
from overbae.core.errors import InputValidationError


def current_runtime():
    environment = os.environ.get("MODAL_ENVIRONMENT", "").strip()
    if not environment:
        raise InputValidationError("Decision serving requires an explicit Modal environment.")
    return {**serving_identity(Path(__file__).resolve().parents[2]), "environment": environment}


def validate_requests(requests):
    try:
        if not isinstance(requests, list) or not 1 <= len(requests) <= 16:
            raise ValueError
        if len(json.dumps(requests, allow_nan=False).encode()) > 256_000:
            raise ValueError
        return [decision_request(request) for request in requests]
    except (ValueError, TypeError) as exc:
        raise InputValidationError(
            "Supply 1-16 input-only decisions with state, question, kind and ordered options (256 KB maximum)."
        ) from exc


def worker(deployed):
    runtime = deployed.serving_runtime
    return modal.Cls.from_name(
        runtime["app"], "DecisionEndpoint", environment_name=runtime["environment"]
    ).with_options(gpu=deployed.gpu_type)(
        weights_path=deployed.weights_path,
        artifact_identity=deployed.checkpoint_hash,
        max_model_len=deployed.max_model_len,
    )
