#!/usr/bin/env python3
"""PreToolUse guard for shell commands."""

import json
import sys

import payload
from guards import check_command


def main():
    verdict = check_command(payload.command(payload.read()))
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
