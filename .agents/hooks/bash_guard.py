#!/usr/bin/env python3
"""PreToolUse guard for shell commands."""

import json
import sys

import payload
from guards import check_command


def main():
    data = payload.read()
    command = payload.command(data)
    verdict = check_command(command, payload.branch(data) if "push" in command else None)
    if not verdict:
        return
    decision, reason = verdict
    # Codex fails an "ask" PreToolUse hook and runs the command anyway.
    if decision == "ask" and "--deny-ask" in sys.argv:
        decision, reason = "deny", f"{reason} Ask the user to run it."
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
