import json
import time
from pathlib import Path

from modal_shared.serving.artifacts import atomic_json

STARTUP_LABELS = {
    "staging_training_files": "Staging training files",
    "initializing_training_runtime": "Initialising training runtime",
    "loading_model": "Loading model",
    "configuring_adapters": "Configuring adapters",
    "verifying_training_tokenizer": "Verifying training tokenizer",
    "loading_training_dataset": "Loading training dataset",
    "building_training_dataset": "Building training dataset",
    "building_validation_dataset": "Building validation dataset",
    "initializing_trainer": "Initialising trainer",
    "starting_optimizer": "Starting optimiser",
}


def read_telemetry(directory):
    try:
        value = json.loads((Path(directory) / "telemetry.json").read_text())
        heartbeat = Path(directory) / "heartbeat.json"
        if heartbeat.exists():
            observed = json.loads(heartbeat.read_text())
            observed["heartbeat_at"] = max(
                value.get("heartbeat_at", 0), observed.get("heartbeat_at", 0)
            )
            value.update(observed)
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
    changed = previous.get("stage") != stage or previous.get("stage_started_at", 0) < previous.get(
        "attempt_started_at", 0
    )
    value = {**previous, "stage": stage, "heartbeat_at": now, "source_at": now, **facts}
    if changed:
        value.update(stage_started_at=now, completed=None, total=None, unit=None)
    if changed or (
        completed is not None
        and (previous.get("completed") is None or completed > previous["completed"])
    ):
        value["last_progress_at"] = now
    for key, item in (("completed", completed), ("total", total), ("unit", unit)):
        if item is not None:
            value[key] = item
    atomic_json(directory / "telemetry.json", value)
    if changed or facts:
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
