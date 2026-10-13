"""Output contains NEEDLE."""

NEEDLE = ""
CASE_INSENSITIVE = True


def evaluate(span):
    output = span.output
    if not NEEDLE or not output:
        return None
    needle = NEEDLE
    if CASE_INSENSITIVE:
        output, needle = output.lower(), needle.lower()
    if needle in output:
        return True, "substring present"
    return False, "substring missing"
