"""Read the hook payload that Claude Code, Codex and Cursor send on stdin.

Codex reports file edits as `apply_patch` with the patch text in
`tool_input.command` instead of a `tool_input.file_path`."""

import json
import os
import re
import subprocess
import sys

_PATCH_PATH = re.compile(
    r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.MULTILINE
)


def read():
    try:
        return json.load(sys.stdin)
    except Exception:
        return {}


def command(payload):
    return (payload.get("tool_input") or {}).get("command", "") or ""


def edited_paths(payload):
    tool_input = payload.get("tool_input") or {}
    cwd = payload.get("cwd") or os.getcwd()
    if tool_input.get("file_path"):
        paths = [tool_input["file_path"]]
    else:
        paths = [m.strip() for m in _PATCH_PATH.findall(tool_input.get("command", "") or "")]
    return [os.path.normpath(os.path.join(cwd, p)) for p in paths]


def project_root(payload):
    root = os.environ.get("CLAUDE_PROJECT_DIR")
    if root:
        return root
    cwd = payload.get("cwd") or os.getcwd()
    try:
        return (
            subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip()
            or cwd
        )
    except Exception:
        return cwd
