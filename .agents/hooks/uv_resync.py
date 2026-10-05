#!/usr/bin/env python3
"""PostToolUse for shell commands: restore the dev/test groups after `uv add` / `uv remove`."""

import contextlib
import subprocess

import payload
from guards import needs_uv_resync


def main():
    data = payload.read()
    if not needs_uv_resync(payload.command(data)):
        return
    with contextlib.suppress(Exception):
        subprocess.run(
            ["uv", "sync", "--group", "dev", "--group", "test"],
            cwd=payload.project_root(data),
            capture_output=True,
            timeout=110,
        )


if __name__ == "__main__":
    main()
