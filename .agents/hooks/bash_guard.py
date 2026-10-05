#!/usr/bin/env python3
"""PreToolUse guard for shell commands."""

import json

import payload
from guards import check_command


def main():
    verdict = check_command(payload.command(payload.read()))
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
