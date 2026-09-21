import hashlib
import json


def row_key(row: dict) -> str:
    payload = {key: row[key] for key in ("messages", "tools") if row.get(key) is not None}
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def materialize_tokens(text, tokens):
    for line in text.splitlines():
        key = row_key(json.loads(line))
        if key not in tokens:
            raise ValueError("A training row was not in the validated preprocessing artifact.")
        yield json.dumps(tokens[key]) + "\n"
