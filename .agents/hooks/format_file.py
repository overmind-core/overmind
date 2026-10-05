#!/usr/bin/env python3
"""PostToolUse formatter for file edits."""

import payload
from formatting import format_path


def main():
    data = payload.read()
    root = payload.project_root(data)
    for path in payload.edited_paths(data):
        format_path(path, root)


if __name__ == "__main__":
    main()
