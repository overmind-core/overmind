#!/usr/bin/env python3
"""PreToolUse guard for Bash commands. Emits a Claude Code permission decision."""

import json
import sys

from guards import check_command


def main():
    try:
        cmd = json.load(sys.stdin).get("tool_input", {}).get("command", "")
    except Exception:
        return
    verdict = check_command(cmd)
    if not verdict:
        return
    decision, reason = verdict
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": decision,
                    "permissionDecisionReason": reason,
                }
            }
        )
    )


if __name__ == "__main__":
    main()
