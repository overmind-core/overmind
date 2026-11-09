import argparse
import base64
import fcntl
import json
import os
import time
from pathlib import Path
from uuid import UUID


def save(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def private(root):
    directory = Path(root) / ".agent"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


def read_json(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def alive(root):
    with (private(root) / "lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True


def exchange(root, payload):
    directory = private(root)
    if "control" in payload:
        save(directory / "control.json", {"state": payload["control"]})
    for message in payload.get("messages", []):
        identity = str(int(message["id"]))
        target = directory / f"input-{identity}.json"
        existing = read_json(target)
        if existing is not None and existing != message:
            raise ValueError("Follow-up conflicts with its retained identity")
        save(target, message)
    for identity, response in payload.get("responses", {}).items():
        identity = str(UUID(identity))
        target = directory / f"response-{identity}.json"
        existing = read_json(target)
        if existing is not None and existing != response:
            raise ValueError("Relay response conflicts with its retained identity")
        save(target, response)
    deadline = time.monotonic() + min(max(float(payload.get("wait", 0)), 0), 1)
    while True:
        snapshot = inspect(root)
        if (
            snapshot["pending"]
            or not snapshot["alive"]
            or snapshot["state"].get("state") in {"completed", "cancelled", "blocked"}
            or time.monotonic() >= deadline
        ):
            return snapshot
        time.sleep(0.025)


def inspect(root):
    directory = private(root)
    pending = []
    for path in sorted(directory.glob("request-*.json")):
        identity = path.stem.removeprefix("request-")
        if not (directory / f"response-{identity}.json").exists():
            pending.append(read_json(path))
    return {
        "alive": alive(root),
        "state": read_json(directory / "state.json", {"state": "not_started"}),
        "pending": pending,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    parser.add_argument("--payload", required=True)
    args = parser.parse_args()
    print(json.dumps(exchange(args.root, json.loads(base64.b64decode(args.payload)))))


if __name__ == "__main__":
    main()
