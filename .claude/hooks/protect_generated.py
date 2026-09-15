#!/usr/bin/env python3
"""PreToolUse guard for Edit/Write: the OpenAPI client is generated — hand
edits are lost on the next `make generate_api_client`."""

import json
import sys

from guards import is_generated_path


def main():
    try:
        path = json.load(sys.stdin).get("tool_input", {}).get("file_path", "")
    except Exception:
        return
    if is_generated_path(path):
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
