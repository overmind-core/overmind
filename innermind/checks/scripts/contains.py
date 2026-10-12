"""Output contains NEEDLE."""

NEEDLE = ""
CASE_INSENSITIVE = True


def output_of(span):
    for key in ("overmind.output", "output"):
        value = span.attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def evaluate(span):
    output = output_of(span)
    if not NEEDLE or not output:
        return None
    needle = NEEDLE
    if CASE_INSENSITIVE:
        output, needle = output.lower(), needle.lower()
    if needle in output:
        return True, "substring present"
    return False, "substring missing"
