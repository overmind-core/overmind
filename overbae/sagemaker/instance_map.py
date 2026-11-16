from __future__ import annotations

INSTANCE_MAP: dict[tuple[str, int], str] = {
    ("A10", 1): "ml.g5.2xlarge",
    ("A10G", 1): "ml.g5.2xlarge",
    ("L4", 1): "ml.g6.2xlarge",
    ("L40S", 1): "ml.g6e.2xlarge",
    ("A100-40GB", 1): "ml.p4d.24xlarge",
    ("A100-40GB", 8): "ml.p4d.24xlarge",
    ("A100", 1): "ml.p4de.24xlarge",
    ("A100-80GB", 1): "ml.p4de.24xlarge",
    ("A100", 4): "ml.p4de.24xlarge",
    ("A100-80GB", 4): "ml.p4de.24xlarge",
    ("A100", 8): "ml.p4de.24xlarge",
    ("A100-80GB", 8): "ml.p4de.24xlarge",
    ("H100", 1): "ml.p5.4xlarge",
    ("H100", 4): "ml.p5.48xlarge",
    ("H100", 8): "ml.p5.48xlarge",
    ("H200", 1): "ml.p5e.48xlarge",
    ("H200", 4): "ml.p5e.48xlarge",
    ("H200", 8): "ml.p5e.48xlarge",
}

INSTANCE_GPU_SPECS: dict[str, tuple[str, int]] = {
    "ml.g5.2xlarge": ("A10", 1),
    "ml.g6.2xlarge": ("L4", 1),
    "ml.g6e.2xlarge": ("L40S", 1),
    "ml.p4d.24xlarge": ("A100-40GB", 8),
    "ml.p4de.24xlarge": ("A100-80GB", 8),
    "ml.p5.4xlarge": ("H100", 1),
    "ml.p5.48xlarge": ("H100", 8),
    "ml.p5e.48xlarge": ("H200", 8),
}


def sagemaker_instance_type(gpu_type: str, gpu_count: int = 1) -> str:
    norm = (gpu_type or "H100").strip().upper()
    count = max(1, int(gpu_count or 1))
    if (norm, count) in INSTANCE_MAP:
        return INSTANCE_MAP[(norm, count)]
    if (norm, 1) in INSTANCE_MAP and count == 1:
        return INSTANCE_MAP[(norm, 1)]
    if norm == "H200":
        return "ml.p5e.48xlarge"
    if norm == "H100":
        return "ml.p5.4xlarge" if count == 1 else "ml.p5.48xlarge"
    if norm.startswith("A100"):
        return "ml.p4d.24xlarge" if "40" in norm else "ml.p4de.24xlarge"
    if norm == "L40S":
        return "ml.g6e.2xlarge"
    if norm == "L4":
        return "ml.g6.2xlarge"
    if norm in ("A10", "A10G"):
        return "ml.g5.2xlarge"
    return "ml.p5.4xlarge"


def gpu_from_instance_type(instance_type: str) -> tuple[str, int]:
    return INSTANCE_GPU_SPECS.get(instance_type, ("H100", 1))


def cuda_visible_devices(gpu_type: str, gpu_count: int = 1) -> str | None:
    """Hide extra GPUs when the SageMaker box is larger than the Modal request.

    ml.p5.4xlarge is 1×H100. There is no 1×H200: ml.p5e.48xlarge is 8×H200,
    and ml.p5.48xlarge is 8×H100. train.py sizes device_map from
    torch.cuda.device_count(), which honors this list.
    """
    _kind, present = gpu_from_instance_type(sagemaker_instance_type(gpu_type, gpu_count))
    requested = max(1, int(gpu_count or 1))
    if present <= requested:
        return None
    return ",".join(str(i) for i in range(requested))
