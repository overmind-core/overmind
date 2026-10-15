"""Output parses as JSON and carries every key in REQUIRED_KEYS.

A key may sit one envelope deep: {"result": {...}} or a list of records that all
carry it. An explicit empty output (null, {} or []) is a rejection, not a
missing key, and abstains.
"""

REQUIRED_KEYS = ()


def parse(text):
    text = text.strip()
    try:
        return json.loads(text)
    except ValueError:
        fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if not fence:
            raise
        return json.loads(fence.group(1))


def records_carry(items, key):
    records = [item for item in items if isinstance(item, dict)]
    return bool(records) and all(key in record for record in records)


def has_key(parsed, key):
    if isinstance(parsed, list):
        return records_carry(parsed, key)
    if not isinstance(parsed, dict):
        return False
    if key in parsed:
        return True
    for value in parsed.values():
        if isinstance(value, dict) and key in value:
            return True
        if isinstance(value, list) and records_carry(value, key):
            return True
    return False


def evaluate(span):
    output = span.output
    if not REQUIRED_KEYS or not output:
        return None
    try:
        parsed = parse(output)
    except ValueError:
        return False, "output is not valid JSON"
    if parsed is None or parsed == {} or parsed == []:
        return None
    missing = [str(key) for key in REQUIRED_KEYS if not has_key(parsed, str(key))]
    if missing:
        return False, "missing keys: " + ", ".join(missing)
    return True, "all required keys present"
