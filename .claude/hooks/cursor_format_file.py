#!/usr/bin/env python3
"""Cursor afterFileEdit formatter. Observational hook — it cannot block, so it
only formats. Cursor runs hooks from the project root."""

import json
import os
import sys

from formatting import format_path


def main():
    try:
        path = json.load(sys.stdin).get("file_path", "")
    except Exception:
        return
    format_path(path, os.getcwd())


if __name__ == "__main__":
    main()
