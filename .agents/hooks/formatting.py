"""Format-only on purpose: lint autofixes (ruff check --fix / biome check --write)
prune imports that later parts of an in-progress file still need."""

import contextlib
import os
import subprocess

from guards import is_generated_path


def format_path(path, root):
    if not path or not os.path.isfile(path) or is_generated_path(path):
        return
    if not os.path.abspath(path).startswith(os.path.abspath(root) + os.sep):
        return
    if path.endswith(".py"):
        cmd, cwd = ["uv", "run", "ruff", "format", "--quiet", path], root
    elif "/frontend/" in path and path.endswith((".ts", ".tsx", ".js", ".jsx", ".json", ".css")):
        cmd, cwd = ["bunx", "biome", "format", "--write", path], os.path.join(root, "frontend")
    else:
        return

    with contextlib.suppress(Exception):
        subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=25)
