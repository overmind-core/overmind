"""Output matches PATTERN, or with MUST_MATCH = False never matches it (a failure-mode gate)."""

import re

PATTERN = ""
MUST_MATCH = True


def output_of(span):
    for key in ("overmind.output", "output"):
        value = span.attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def evaluate(span):
    output = output_of(span)
    if not PATTERN or not output:
        return None
    found = re.search(PATTERN, output) is not None
    if found == MUST_MATCH:
        return True, "pattern matched" if found else "pattern absent"
    return False, "pattern absent" if MUST_MATCH else "pattern matched"
