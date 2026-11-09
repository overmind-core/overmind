import hashlib
import importlib
import importlib.metadata
import json
import platform
import sys
from pathlib import Path

from packaging.requirements import Requirement

ROOT = Path("/opt/overmind-runtime")
FILES = (
    "workshop_runtime/environment.py",
    "workshop_runtime/data.py",
    "workshop_runtime/observations.py",
    "workshop_runtime/skills.py",
    "workshop_runtime/skills/decision-data/SKILL.md",
    "workshop_runtime/skills/conversation-data/SKILL.md",
    "workshop_runtime/session.py",
    "workshop_runtime/agent.py",
    "workshop_runtime/mailbox.py",
    "workshop_runtime/presentation.py",
    "workshop_runtime/presentation-catalog.json",
    "workshop_runtime/replay.py",
    "workshop_runtime/isolate.py",
    "overbae/core/errors.py",
    "overbae/services/datasets/partition_assignment.py",
    "overbae/services/datasets/partition.py",
    "overbae/services/datasets/row_values.py",
    "overbae/services/datasets/examples.py",
    "overbae/services/datasets/package_contract.py",
    "overbae/services/datasets/pipeline_graph.py",
    "workshop_runtime/GUIDE.md",
    "modal_shared/decisions.py",
)


def identity(root=None):
    root = Path(root) if root else Path(__file__).resolve().parent.parent
    requirements = root / "docker/workshop/requirements.txt"
    if not requirements.exists():
        requirements = ROOT / "requirements.txt"
    digest = hashlib.sha256(requirements.read_bytes())
    for name in FILES:
        digest.update(name.encode() + b"\0" + (root / name).read_bytes())
    return "sha256:" + digest.hexdigest()


def doctor(requirements=None):
    requirements = Path(requirements) if requirements else ROOT / "requirements.txt"
    mismatches = {}
    versions = {}
    for line in requirements.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        dependency = Requirement(line)
        if dependency.marker and not dependency.marker.evaluate():
            continue
        try:
            version = importlib.metadata.version(dependency.name)
        except importlib.metadata.PackageNotFoundError:
            version = None
        versions[dependency.name] = version
        if version is None or version not in dependency.specifier:
            mismatches[dependency.name] = {
                "expected": str(dependency.specifier),
                "installed": version,
            }
    if sys.version_info[:2] != (3, 13):
        mismatches["python"] = {"expected": "3.13", "installed": platform.python_version()}
    for module in (
        "pandas",
        "polars",
        "numpy",
        "pyarrow",
        "duckdb",
        "scipy",
        "sklearn",
        "matplotlib",
        "openpyxl",
        "rapidfuzz",
        "datasketch",
        "jsonschema",
        "ftfy",
        "regex",
        "tiktoken",
        "ipykernel",
        "jupyter_client",
        "pydantic_ai",
        "openai",
    ):
        try:
            importlib.import_module(module)
        except Exception as exc:
            mismatches[module] = {"import_error": type(exc).__name__}
    return {
        "ready": not mismatches,
        "identity": identity(),
        "python": platform.python_version(),
        "packages": versions,
        "mismatches": mismatches,
    }


if __name__ == "__main__":
    report = doctor(sys.argv[1] if len(sys.argv) > 1 else None)
    print(json.dumps(report, sort_keys=True))
    sys.exit(0 if report["ready"] else 1)
