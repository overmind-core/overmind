"""
Utility functions for the policies endpoint.
"""

import hashlib
import json
from typing import Any, Optional, Dict


def to_plain(obj: Any) -> Any:
    """
    Convert a Pydantic model to a plain dictionary.

    Args:
        obj: Object to convert (Pydantic model or plain dict/value)

    Returns:
        Plain dictionary or the original object if not a Pydantic model
    """
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def compute_policies_hash(
    llm_policy: Optional[Dict[str, Any]],
    mcp_policy: Optional[Any],
    chatbot_policy: Optional[Any],
) -> str:
    """
    Compute a deterministic hash of policy data.

    Creates a canonical JSON representation with sorted keys and
    computes its SHA-256 hash. Used to detect policy changes.

    Args:
        llm_policy: LLM policy configuration
        mcp_policy: MCP policy configuration
        chatbot_policy: Chatbot policy configuration

    Returns:
        Hexadecimal SHA-256 hash string
    """
    canonical = json.dumps(
        {
            "llm_policy": to_plain(llm_policy),
            "mcp_policy": to_plain(mcp_policy),
            "chatbot_policy": to_plain(chatbot_policy),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_json_out_of_sse(response_content: bytes) -> dict:
    """
    Extract JSON data from Server-Sent Events (SSE) response.

    Parses SSE format looking for lines starting with "data: {" and
    extracts the JSON payload.

    Args:
        response_content: Raw bytes from SSE response

    Returns:
        Parsed JSON dictionary

    Raises:
        Exception: If multiple JSON strings are found or parsing fails
    """
    decoded_response = response_content.decode("utf-8")

    lines = decoded_response.strip().split("\n")

    json_strings = []
    for line in lines:
        if line.startswith("data: {"):
            json_strings.append(line.removeprefix("data: "))

    if len(json_strings) == 1:
        return json.loads(json_strings[0])
    elif len(json_strings) > 1:
        raise Exception(f"Found many json strings: {json_strings}")

    raise Exception("No JSON data found in SSE response")
