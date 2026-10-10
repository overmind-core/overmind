#!/usr/bin/env python3
"""PreToolUse guard for file edits: the OpenAPI client is generated — hand
edits are lost on the next `make generate_api_client`."""

import json

import payload
from guards import is_generated_path


def main():
    if any(is_generated_path(p) for p in payload.edited_paths(payload.read())):
        print(
            json.dumps(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": (
                            "frontend/src/openapi/ is generated. Change the backend API and run "
                            "`make generate_api_client` instead (see the api-endpoints skill)."
                        ),
                    }
                }
            )
        )


if __name__ == "__main__":
    main()
