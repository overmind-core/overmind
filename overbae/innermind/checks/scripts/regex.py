"""Output matches PATTERN, or with MUST_MATCH = False never matches it (a failure-mode gate)."""

PATTERN = ""
MUST_MATCH = True


def evaluate(span):
    output = span.output
    if not PATTERN or not output:
        return None
    found = re.search(PATTERN, output) is not None
    if found == MUST_MATCH:
        return True, "pattern matched" if found else "pattern absent"
    return False, "pattern absent" if MUST_MATCH else "pattern matched"
