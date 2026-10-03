import hashlib
from pathlib import Path

from modal_shared.preparation import processor_fingerprint, training_fingerprint


def data_format_identity(root):
    digest = hashlib.sha256()
    for relative in (
        "overbae/services/datasets/examples.py",
        "overbae/services/finetuning_validator.py",
        "overbae/services/finetuning_tool_validation.py",
    ):
        digest.update((Path(root) / relative).read_bytes())
    return digest.hexdigest()


def identity(root):
    root = Path(root)
    assets = root / "overbae/services/sft_assets"
    digest = hashlib.sha256()
    for folder in (root / "modal_shared", assets):
        for path in sorted(folder.rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    digest.update((root / "overbae/modal/modal_sft_worker.py").read_bytes())
    data_format = data_format_identity(root)
    digest.update(data_format.encode())
    return {
        "app": f"overmind-sft-{digest.hexdigest()[:24]}",
        "release": digest.hexdigest(),
        "processor": processor_fingerprint(assets),
        "training": training_fingerprint(assets),
        "data_format": data_format,
    }


def evaluation_identity(root):
    root = Path(root)
    release = identity(root)
    worker = root / "overbae/modal/modal_decision_evaluation.py"
    digest = hashlib.sha256((release["release"] + worker.read_text()).encode()).hexdigest()
    return {"app": "overmind-decision-eval-" + digest[:24], "release": digest}
