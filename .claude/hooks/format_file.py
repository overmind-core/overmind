#!/usr/bin/env python3
"""PostToolUse formatter for Edit/Write."""

import json
import os
import sys

from formatting import format_path


def main():
    try:
        path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
    except Exception:
        return
    format_path(path, os.environ.get("CLAUDE_PROJECT_DIR", "."))


if __name__ == "__main__":
    main()
