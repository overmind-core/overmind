"""Every marker in GOAL_MARKERS appears in the output, case-insensitively."""

GOAL_MARKERS = ()


def output_of(span):
    for key in ("overmind.output", "output"):
        value = span.attributes.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def evaluate(span):
    output = output_of(span).lower()
    if not GOAL_MARKERS or not output:
        return None
    missing = [marker for marker in GOAL_MARKERS if str(marker).lower() not in output]
    found = len(GOAL_MARKERS) - len(missing)
    reason = f"{found}/{len(GOAL_MARKERS)} goal markers present"
    if missing:
        return False, reason + "; missing: " + ", ".join(str(m) for m in missing)
    return True, reason
