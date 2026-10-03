import json
import time
from pathlib import Path

from modal_shared.serving.artifacts import atomic_json


def read_telemetry(directory):
    try:
        value = json.loads((Path(directory) / "telemetry.json").read_text())
        heartbeat = Path(directory) / "heartbeat.json"
        if heartbeat.exists():
            value.update(json.loads(heartbeat.read_text()))
        return value
    except (OSError, ValueError):
        return {}


def record_stage(directory, stage, *, completed=None, total=None, unit=None, **facts):
    if directory is None:
        return
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    now = time.time()
    previous = read_telemetry(directory)
    value = {**previous, "stage": stage, "heartbeat_at": now, **facts}
    if previous.get("stage") != stage:
        value.update(stage_started_at=now, completed=None, total=None, unit=None)
    for key, item in (("completed", completed), ("total", total), ("unit", unit)):
        if item is not None:
            value[key] = item
    atomic_json(directory / "telemetry.json", value)
    if previous.get("stage") != stage or facts:
        with (directory / "stages.jsonl").open("a") as stream:
            stream.write(json.dumps(value) + "\n")


def record_heartbeat(directory, *, new_attempt=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "heartbeat.json"
    previous = json.loads(path.read_text()) if path.exists() else {}
    now = time.time()
    value = {**previous, "heartbeat_at": now}
    if new_attempt or not previous:
        value.update(
            attempt=previous.get("attempt", 0) + 1,
            attempt_started_at=now,
            overall_started_at=previous.get("overall_started_at", now),
        )
        with (directory / "attempts.jsonl").open("a") as output:
            output.write(json.dumps(value) + "\n")
    atomic_json(path, value)
    return value
