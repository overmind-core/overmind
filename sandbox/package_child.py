import json
import os
import resource
import runpy
import sys
from pathlib import Path


def main():
    cpu_seconds, file_bytes, processes, descriptors = json.loads(sys.argv[1])
    for kind, value in (
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, file_bytes),
        (resource.RLIMIT_NOFILE, descriptors),
    ):
        resource.setrlimit(kind, (value, value))
    if processes:
        resource.setrlimit(resource.RLIMIT_NPROC, (processes, processes))
    request = json.loads(Path("request.json").read_text())
    if hasattr(os, "sched_getaffinity"):
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[: request["cpus"]])
    root = Path.cwd()
    entrypoint = root / "package" / request["entrypoint"]
    sys.path.insert(0, str(entrypoint.parent))
    sys.path.insert(0, str(root / "package"))
    sys.argv = [
        str(entrypoint),
        str(root / "input.jsonl"),
        str(root / "output.jsonl"),
        str(root / "parameters.json"),
    ]
    runpy.run_path(str(entrypoint), run_name="__main__")


if __name__ == "__main__":
    main()
