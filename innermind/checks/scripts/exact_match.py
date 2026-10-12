"""Output equals REFERENCE."""

REFERENCE = ""
CASE_INSENSITIVE = True


def output_of(span):
    for key in ("overmind.output", "output"):
        value = span.attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def evaluate(span):
    output = output_of(span)
    if not REFERENCE or not output:
        return None
    got, want = output, REFERENCE.strip()
    if CASE_INSENSITIVE:
        got, want = got.lower(), want.lower()
    if got == want:
        return True, "exact match"
    return False, "output differs from the reference"
