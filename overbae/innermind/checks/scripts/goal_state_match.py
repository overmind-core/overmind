"""Every marker in GOAL_MARKERS appears in the output, case-insensitively."""

GOAL_MARKERS = ()


def evaluate(span):
    output = span.output.lower()
    if not GOAL_MARKERS or not output:
        return None
    missing = [marker for marker in GOAL_MARKERS if str(marker).lower() not in output]
    found = len(GOAL_MARKERS) - len(missing)
    reason = f"{found}/{len(GOAL_MARKERS)} goal markers present"
    if missing:
        return False, reason + "; missing: " + ", ".join(str(m) for m in missing)
    return True, reason
