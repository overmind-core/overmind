"""Output that opens as JSON, bare or in a ``` fence, parses. Plain text abstains."""

import json
import re


def output_of(span):
    for key in ("overmind.output", "output"):
        value = span.attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def evaluate(span):
    text = output_of(span)
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1)
    if not text.startswith(("{", "[")):
        return None
    try:
        json.loads(text)
    except ValueError as exc:
        return False, "output is not valid JSON: " + str(exc)
    return True, "output is valid JSON"
