import re
from pathlib import Path


def failure_receipt(log_path):
    try:
        with Path(log_path).open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - 65536))
            tail = stream.read().decode(errors="replace")
    except OSError:
        tail = ""
    exhausted = bool(re.search(r"(?m)^torch\.OutOfMemoryError: CUDA out of memory", tail))
    return {
        "code": "gpu_memory_exhausted" if exhausted else "training_process_failed",
        "message": (
            "Training exceeded the worker's GPU memory capacity. Retained checks remain available."
            if exhausted
            else "Training stopped with a worker error. Retained checks remain available."
        ),
        "basis": "recorded worker exception" if exhausted else "unclassified worker failure",
    }
