import hashlib
import json


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read(path):
    with open(path) as stream:
        return [json.loads(line) for line in stream]


def write(records, path):
    with open(path, "w") as stream:
        for row in records:
            stream.write(canonical(row) + "\n")
