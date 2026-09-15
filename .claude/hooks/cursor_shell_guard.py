#!/usr/bin/env python3
"""Cursor beforeShellExecution guard. Same rules as the Claude Code hook."""

import json
import sys

from guards import check_command


def main():
    try:
        cmd = json.load(sys.stdin).get("command", "")
    except Exception:
        return
    verdict = check_command(cmd)
    if not verdict:
        # No opinion. An explicit "allow" would bypass Cursor's own prompts for
        # every unmatched command, so emit an empty decision object instead.
        print("{}")
        return
    decision, reason = verdict
    print(json.dumps({"permission": decision, "agent_message": reason, "user_message": reason}))


if __name__ == "__main__":
    main()
