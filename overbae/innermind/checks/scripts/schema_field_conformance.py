"""Share of the FIELDS present in the JSON output that follow their rules.

FIELDS maps a field name to any of: "type" (string, number, integer, boolean,
array, object), "enum", "min", "max", "pattern", "nullable". Presence is
json_required_keys' job, so an absent field is skipped, not failed.

    FIELDS = {"status": {"enum": ["open", "closed"]}, "total": {"type": "number", "min": 0}}
"""

FIELDS = {}


def parse(text):
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if not fence:
            raise
        return json.loads(fence.group(1))


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


TYPE_CHECKS = {
    "number": is_number,
    "integer": lambda value: isinstance(value, int) and not isinstance(value, bool),
    "string": lambda value: isinstance(value, str),
    "boolean": lambda value: isinstance(value, bool),
    "array": lambda value: isinstance(value, list),
    "object": lambda value: isinstance(value, dict),
}


def values_of(parsed, name):
    """The field's values, tolerating one envelope level like json_required_keys."""
    if isinstance(parsed, list):
        records = [item for item in parsed if isinstance(item, dict)]
        if records and all(name in record for record in records):
            return [record[name] for record in records]
        return []
    if not isinstance(parsed, dict):
        return []
    if name in parsed:
        return [parsed[name]]
    for value in parsed.values():
        if isinstance(value, dict) and name in value:
            return [value[name]]
        if isinstance(value, list):
            nested = values_of(value, name)
            if nested:
                return nested
    return []


def violation(value, rules):
    if value is None:
        return "" if rules.get("nullable") else "null but not nullable"
    expected = rules.get("type")
    if expected in TYPE_CHECKS and not TYPE_CHECKS[expected](value):
        return f"expected {expected}"
    enum = rules.get("enum")
    if enum and isinstance(value, str):
        allowed = {str(item).strip().lower() for item in enum}
        if value.strip().lower() not in allowed:
            return f"{value!r} not in {sorted(allowed)}"
    if is_number(value):
        low, high = rules.get("min"), rules.get("max")
        if low is not None and value < low:
            return f"{value} below minimum {low}"
        if high is not None and value > high:
            return f"{value} above maximum {high}"
    pattern = rules.get("pattern")
    if pattern and isinstance(value, str) and re.search(pattern, value) is None:
        return f"{value!r} does not match {pattern!r}"
    return ""


def evaluate(span):
    output = span.output
    if not FIELDS or not output:
        return None
    try:
        parsed = parse(output)
    except ValueError:
        return 0.0, "output is not valid JSON"
    checked = 0
    failures = []
    for name, rules in FIELDS.items():
        values = values_of(parsed, str(name))
        if not values:
            continue
        checked += 1
        reasons = [reason for reason in (violation(v, rules) for v in values) if reason]
        if reasons:
            failures.append(f"{name}: {reasons[0]}")
    if not checked:
        return None
    reason = f"{checked - len(failures)}/{checked} present fields conform"
    if failures:
        reason += "; " + "; ".join(failures)
    return (checked - len(failures)) / checked, reason
