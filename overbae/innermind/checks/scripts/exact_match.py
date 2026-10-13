"""Output equals REFERENCE."""

REFERENCE = ""
CASE_INSENSITIVE = True


def evaluate(span):
    output = span.output
    if not REFERENCE or not output:
        return None
    got, want = output, REFERENCE.strip()
    if CASE_INSENSITIVE:
        got, want = got.lower(), want.lower()
    if got == want:
        return True, "exact match"
    return False, "output differs from the reference"
