# /// script
# requires-python = ">=3.12"
# dependencies = ["daytona==0.227.0"]
# ///
import argparse
import fcntl
import hashlib
import json
import os
import sys
import time
import uuid
from pathlib import Path

from daytona import (
    CreateSandboxFromImageParams,
    Daytona,
    DaytonaConfig,
    Image,
    Resources,
    SessionExecuteRequest,
)

API_URL = "https://app.daytona.io/api"
NAME = "overmind-native-poc"
INPUT = b"group,value\na,1\nb,4\na,3\n"
PROGRAM = """import hashlib,json, pathlib,polars as pl
root=pathlib.Path('/home/daytona/overmind-poc')
rows=pl.read_csv(root/'input.csv').group_by('group').agg(pl.col('value').sum()).sort('group').to_dicts()
result={'rows':rows,'input_sha256':hashlib.sha256((root/'input.csv').read_bytes()).hexdigest(),'polars':pl.__version__}
(root/'result.json').write_text(json.dumps(result,sort_keys=True))
print(json.dumps(result,sort_keys=True))
"""
ROOT = "/home/daytona/overmind-poc"


def save(path, record):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w") as handle:
        handle.write(json.dumps(record, indent=2) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    temporary.replace(path)


def evidence(sandbox):
    sandbox.refresh_data(request_timeout=30)
    fields = [
        "id",
        "name",
        "state",
        "snapshot",
        "cpu",
        "memory",
        "disk",
        "auto_stop_interval",
        "auto_pause_interval",
        "auto_delete_interval",
        "network_block_all",
        "auto_destroy_at",
    ]
    result = {field: getattr(sandbox, field, None) for field in fields}
    if result["auto_stop_interval"] != 0 or result["auto_pause_interval"] not in (0, None):
        raise RuntimeError("Workspace is not configured to remain running")
    if result["auto_delete_interval"] != -1 or result["network_block_all"] is not True:
        raise RuntimeError("Workspace retention or network policy mismatch")
    if result["auto_destroy_at"] is not None:
        raise RuntimeError("Workspace has a destructive TTL")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["provision", "run", "inspect", "restart"])
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--key-file", type=Path)
    parser.add_argument("--simulate-lost-ack", action="store_true")
    args = parser.parse_args()
    key = args.key_file.read_text().strip() if args.key_file else os.environ["DAYTONA_API_KEY"]
    client = Daytona(DaytonaConfig(api_key=key, api_url=API_URL, otel_enabled=False))
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock = args.state.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    record = json.loads(args.state.read_text()) if args.state.exists() else {}
    if args.action == "provision":
        matches = [s for s in client.list(request_timeout=30) if s.name == NAME]
        if len(matches) > 1:
            raise RuntimeError("Ambiguous workspace identity")
        if matches:
            sandbox = matches[0]
            if sandbox.labels.get("owner") != "overmind-native-poc":
                raise RuntimeError("Existing name is not owned by this probe")
        elif record.get("provision_intent"):
            raise RuntimeError("Creation outcome uncertain; refusing another submission")
        else:
            record["provision_intent"] = {"name": NAME, "at": time.time()}
            save(args.state, record)
            sandbox = client.create(
                CreateSandboxFromImageParams(
                    name=NAME,
                    image=Image.base("python:3.12.8-slim").pip_install("polars==1.35.2"),
                    resources=Resources(cpu=1, memory=2, disk=3),
                    labels={"owner": "overmind-native-poc"},
                    public=False,
                    auto_stop_interval=0,
                    auto_pause_interval=0,
                    auto_delete_interval=-1,
                    ephemeral=False,
                    network_block_all=True,
                ),
                timeout=180,
            )
            if args.simulate_lost_ack:
                print("Creation response intentionally discarded; rerun provision to reconcile.")
                return
        record["workspace"] = evidence(sandbox)
        save(args.state, record)
        print(json.dumps(record["workspace"], default=str))
        return
    sandbox = client.get(record["workspace"]["id"], request_timeout=30)
    if args.action == "restart":
        record["restart_intent"] = time.time()
        save(args.state, record)
        try:
            sandbox.stop(timeout=60)
        finally:
            sandbox.start(timeout=90)
        record["restarted_at"] = time.time()
    if args.action == "run":
        if record.get("command_intent"):
            raise RuntimeError("Command already submitted or uncertain; use inspect, not run")
        # These fixed setup operations are idempotent and run before the measured command.
        setup = sandbox.process.exec(f"mkdir -p {ROOT}", timeout=30)
        if setup.exit_code != 0:
            raise RuntimeError("Workspace directory setup failed")
        sandbox.fs.upload_file(INPUT, f"{ROOT}/input.csv")
        sandbox.fs.upload_file(PROGRAM.encode(), f"{ROOT}/transform.py")
        if sandbox.fs.download_file(f"{ROOT}/input.csv") != INPUT:
            raise RuntimeError("Input transfer mismatch")
        session = "probe-" + uuid.uuid4().hex
        record["command_intent"] = {"session": session, "command": f"python {ROOT}/transform.py"}
        save(args.state, record)
        sandbox.process.create_session(session, request_timeout=30)
        response = sandbox.process.execute_session_command(
            session,
            SessionExecuteRequest(command=record["command_intent"]["command"], run_async=True),
            timeout=30,
        )
        if args.simulate_lost_ack:
            print("Command response intentionally discarded; inspect in a new process.")
            return
        record["command_id"] = response.cmd_id
        save(args.state, record)
    if (
        record.get("command_intent")
        and args.action != "restart"
        and record.get("exit_code") is None
    ):
        session = record["command_intent"]["session"]
        commands = sandbox.process.get_session(session, request_timeout=30).commands or []
        if len(commands) != 1 or commands[0].command != record["command_intent"]["command"]:
            raise RuntimeError("Command outcome uncertain; no resubmission")
        command_id = commands[0].id
        deadline = time.monotonic() + 60
        while True:
            command = sandbox.process.get_session_command(session, command_id, request_timeout=15)
            if command.exit_code is not None:
                break
            if time.monotonic() >= deadline:
                raise RuntimeError("Command still pending; inspect again")
            time.sleep(1)
        if command.exit_code != 0:
            raise RuntimeError("Transformation failed; inspect provider command logs")
        record["command_id"] = command_id
        record["exit_code"] = command.exit_code
        logs = sandbox.process.get_session_command_logs(session, command_id, request_timeout=15)
        record["logs"] = {"stdout": logs.stdout, "stderr": logs.stderr}
    if record.get("command_intent"):
        artifact = sandbox.fs.download_file(f"{ROOT}/result.json")
        result = json.loads(artifact)
        if result["rows"] != [{"group": "a", "value": 4}, {"group": "b", "value": 4}]:
            raise RuntimeError("Incorrect grouped output")
        if result["input_sha256"] != hashlib.sha256(INPUT).hexdigest():
            raise RuntimeError("Input checksum mismatch")
        if result["polars"] != "1.35.2":
            raise RuntimeError("Dependency version mismatch")
        digest = hashlib.sha256(artifact).hexdigest()
        if record.get("output_sha256") and digest != record["output_sha256"]:
            raise RuntimeError("Artifact changed across reconnection")
        record["output_sha256"] = digest
        record["result"] = result
        args.state.with_suffix(".result.json").write_bytes(artifact)
    record["workspace"] = evidence(sandbox)
    record.setdefault("observations", []).append({"at": time.time(), "action": args.action})
    save(args.state, record)
    print(json.dumps(record, default=str))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # SDK exceptions can contain request URLs and credentials; never print their bodies.
        print(
            f"Probe stopped: {type(exc).__name__}. Inspect the retained receipt; do not resubmit uncertain work.",
            file=sys.stderr,
        )
        sys.exit(1)
