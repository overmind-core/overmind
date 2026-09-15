#!/usr/bin/env python3
"""PostToolUse for Bash: restore the dev/test groups after `uv add` / `uv remove`."""

import contextlib
import json
import os
import subprocess
import sys

from guards import needs_uv_resync


def main():
    try:
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    except Exception:
        return
    if not needs_uv_resync(cmd):
        return
    root = os.environ.get("CLAUDE_PROJECT_DIR", ".")
    with contextlib.suppress(Exception):
        subprocess.run(
            ["uv", "sync", "--group", "dev", "--group", "test"],
            cwd=root,
            capture_output=True,
            timeout=110,
        )


if __name__ == "__main__":
    main()
