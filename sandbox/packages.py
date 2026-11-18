"""Retained packages run inside the existing sandbox isolation boundary."""

import hashlib
import json
import platform
from functools import cache
from pathlib import Path, PurePosixPath

MAX_PACKAGE_BYTES = 20 * 1024**2
MAX_OUTPUT_BYTES = 2 * 1024**3
MAX_BATCHES = 10_000


IO = ("jsonl", "parquet")


@cache
def runtime_id():
    """What a script can observe: its I/O contract, the interpreter, the libraries it can
    import and the runner that confines it. A change to the server leaves it unchanged."""
    root = Path(__file__).resolve().parent
    # The interpreter's version, not its build string: one runtime on every host OS.
    interpreter = f"{platform.python_implementation()} {platform.python_version()}"
    digest = hashlib.sha256(b"overmind-sandbox-package-v3\0" + interpreter.encode() + b"\0")
    for path in (
        root.parent / "uv.lock",
        root / "package_child.py",
        root / "kernel.py",
        root / "script_io.py",
    ):
        digest.update(path.relative_to(root.parent).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def validate(header):
    if header.get("runtime") != runtime_id():
        raise ValueError(
            "The pinned sandbox runtime is unavailable. Save a new package revision with the advertised runtime identity."
        )
    size = header.get("package_bytes")
    if type(size) is not int or not 0 < size <= MAX_PACKAGE_BYTES:
        raise ValueError("package_bytes must be between 1 and 20 MiB.")
    if not isinstance(header.get("entrypoint"), str):
        raise ValueError("A package entrypoint is required.")
    if not isinstance(header.get("parameters"), dict):
        raise ValueError("Package parameters must be an object.")
    if header.get("io", "jsonl") not in IO:
        raise ValueError("Package io is jsonl or parquet.")
    for name in ("expanded_bytes", "rows"):
        if type(header.get(name, 0)) is not int or header.get(name, 0) < 0:
            raise ValueError(f"{name} must be a whole number.")
    batch_rows = header.get("batch_rows")
    if batch_rows is not None and (type(batch_rows) is not int or not 1 <= batch_rows <= 100000):
        raise ValueError("batch_rows must be between 1 and 100000.")
    # Each batch adds one output segment length to the reply header.
    rows = header.get("rows", 0)
    if batch_rows and rows and -(-rows // batch_rows) > MAX_BATCHES:
        raise ValueError(f"batch_rows must leave at most {MAX_BATCHES:,} batches.")
    bounds = {
        "seconds": (1, 1200),
        "memory_mb": (64, 4096),
        "scratch_mb": (16, 4096),
        "cpus": (1, 4),
    }
    limits = header.get("limits", {})
    if not isinstance(limits, dict) or limits.keys() - bounds.keys():
        raise ValueError("Unknown package resource limit.")
    for name, value in limits.items():
        low, high = bounds[name]
        if type(value) is not int or not low <= value <= high:
            raise ValueError(f"Invalid {name} limit.")


def unpack(header, scratch):
    files = json.loads((scratch / "package.json").read_bytes())
    if not isinstance(files, dict) or not 1 <= len(files) <= 100:
        raise ValueError("Packages accept 1–100 files.")
    if header["entrypoint"] not in files or not header["entrypoint"].endswith(".py"):
        raise ValueError("The entrypoint must name a retained Python file.")
    total = 0
    for name, content in files.items():
        path = PurePosixPath(name)
        if (
            path.is_absolute()
            or ".." in path.parts
            or "\\" in name
            or "\0" in name
            or path.as_posix() != name
            or path.suffix not in {".py", ".json", ".txt", ".lock"}
            or not isinstance(content, str)
        ):
            raise ValueError(
                "Packages accept relative regular Python, JSON, text and lock files only."
            )
        data = content.encode()
        total += len(data)
        if total > 10 * 1024**2:
            raise ValueError("The expanded package exceeds 10 MiB.")
        destination = scratch / "package" / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
    (scratch / "package.json").unlink()
    (scratch / "parameters.json").write_text(json.dumps(header["parameters"], allow_nan=False))
    (scratch / "request.json").write_text(
        json.dumps(
            {
                "entrypoint": header["entrypoint"],
                "cpus": header.get("limits", {}).get("cpus", 2),
                "io": header.get("io", "jsonl"),
            }
        )
    )
