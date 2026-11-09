import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
from contextlib import suppress
from pathlib import Path
from uuid import UUID

from modal_shared.decisions import decision_line, decision_request
from overbae.services.datasets import package_contract, partition_assignment, pipeline_graph
from overbae.services.datasets.partition import inherit_provenance
from overbae.services.datasets.row_values import normalize_json_rows
from workshop_runtime import data, session
from workshop_runtime.environment import identity


def canonical(records):
    return hashlib.sha256(
        json.dumps(
            records, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
    ).hexdigest()


def read_rows(path):
    return list(data.iter_jsonl(path))


def write_rows(path, records):
    with Path(path).open("w") as stream:
        for record in records:
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")


def replay(root, replay_id, package, parameters=None, partition=None):
    root = Path(root).resolve()
    replay_id = str(UUID(replay_id))
    destination = root / ".agent" / "replays" / replay_id
    destination.mkdir(parents=True, exist_ok=True, mode=0o700)
    receipt_path = destination / "receipt.json"
    existing = json.loads(receipt_path.read_text()) if receipt_path.exists() else None
    if existing:
        return existing
    if (destination / "intent.json").exists():
        raise RuntimeError("Replay was interrupted; inspect retained files before a new attempt")
    package_path = (root / "work" / package).resolve()
    if not package_path.is_relative_to(root / "work") or not package_path.is_file():
        raise ValueError("Package must be a regular file inside the working directory")
    payload = package_path.read_bytes()
    manifest, _, files = package_contract.inspect_bytes(payload, runtime=identity())
    parameters = parameters or {}
    if set(parameters) != set(manifest.get("parameters", {})):
        raise ValueError("Supply exactly the package's declared parameters")
    list(package_contract.check_rows([parameters], manifest.get("parameters", {})))
    if any(step.get("batch_rows") for step in manifest["steps"]):
        raise ValueError(
            "Native replay executes full step populations; omit batch_rows and stream within your script"
        )
    partition = partition_assignment.validate_recipe(partition) if partition else None
    session.save(
        destination / "intent.json",
        {
            "package_sha256": hashlib.sha256(payload).hexdigest(),
            "parameters": parameters,
            "partition": partition,
        },
    )
    (destination / "package.zip").write_bytes(payload)
    source = data.records(data.read_frame(root / "inputs/input.parquet"))
    lineage_source = source
    source = data.replay_inputs(source)
    graph = pipeline_graph.describe(manifest["steps"], files=files)
    values = {"source": source}
    lineages = {"source": lineage_source}
    manifest_path = root / "context/input.json"
    input_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    source_groups = input_manifest.get("group_columns", [])
    receipts = []
    result = {
        "id": replay_id,
        "runtime": identity(),
        "package_sha256": hashlib.sha256(payload).hexdigest(),
        "source_sha256": hashlib.sha256((root / "inputs/input.parquet").read_bytes()).hexdigest(),
        "parameters": parameters,
        "partition_recipe": partition,
        "steps": receipts,
    }
    try:
        for index, (step, node) in enumerate(zip(manifest["steps"], graph["nodes"], strict=True)):
            directory = destination / f"step-{index}"
            directory.mkdir(mode=0o755)
            # The replay UID can traverse only this retained run, not the protected journal.
            destination.chmod(0o711)
            destination.parent.chmod(0o711)
            (root / ".agent").chmod(0o711)
            for name, content in files.items():
                target = directory / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
            incoming = [row for parent in node["inputs"] for row in values[parent]]
            if len(node["inputs"]) > 1 and len({row["source_row"] for row in incoming}) != len(
                incoming
            ):
                raise ValueError("Merged branches overlap in source_row identity")
            list(package_contract.check_rows(incoming, step.get("input_schema", {})))
            write_rows(directory / "input.jsonl", incoming)
            (directory / "parameters.json").write_text(json.dumps(parameters))
            os.chown(directory, 10002, 10002)
            seconds = manifest.get("limits", {}).get("seconds", 120)
            with (destination / f"step-{index}.log").open("wb") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "workshop_runtime.isolate",
                        str(directory),
                        str(seconds),
                        step["entrypoint"],
                        "input.jsonl",
                        "output.jsonl",
                        "parameters.json",
                    ],
                    cwd=directory,
                    env=session.kernel_environment(directory),
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    process.wait(timeout=seconds + 5)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
                    raise ValueError("Replay exceeded its declared execution time") from None
                finally:
                    # A successful parent must not leave background work mutating retained outputs.
                    with suppress(ProcessLookupError):
                        os.killpg(process.pid, signal.SIGKILL)
            if process.returncode:
                raise ValueError((destination / f"step-{index}.log").read_text()[-8000:])
            output_path = directory / "output.jsonl"
            if output_path.is_symlink() or not output_path.is_file():
                raise ValueError("Replay must produce a regular output.jsonl")
            produced = read_rows(output_path)
            list(package_contract.check_rows(produced, step.get("output_schema", {})))
            for row in produced:
                if step.get("consumer") == "decision_train":
                    decision_line(row)
                elif step.get("consumer") == "decision_eval":
                    decision_request(row["input"]["decision"])
            checks = step.get("checks", {})
            if checks.get("preserve_rows") and len(produced) != len(incoming):
                raise ValueError("Replay violated preserve_rows")
            if len(produced) < checks.get("min_rows", 0) or len(produced) > checks.get(
                "max_rows", float("inf")
            ):
                raise ValueError("Replay violated declared population bounds")
            retained = destination / f"output-{index}.jsonl"
            write_rows(retained, produced)
            receipts.append(
                {
                    "index": index,
                    "input_sha256": canonical(incoming),
                    "output_sha256": canonical(produced),
                    "rows": len(produced),
                    "exit_code": process.returncode,
                }
            )
            parents = [row for parent in node["inputs"] for row in lineages[parent]]
            original = {row["source_row"]: row for row in parents}
            if len(original) != len(parents):
                raise ValueError("Replay inputs have ambiguous source_row identities")
            next_id = max(original, default=0) + 1
            assigned = set()
            derived = []
            inherited = {
                "trace_id",
                "source_trace_id",
                "conversation_id",
                *source_groups,
            } & set().union(*(row.keys() for row in parents))
            for row in normalize_json_rows(produced):
                row = dict(row)
                explicit = row.pop("_overmind_parent_rows", None)
                parent_ids = explicit if explicit is not None else [row.get("source_row")]
                if not isinstance(parent_ids, list) or not parent_ids or len(parent_ids) > 10000:
                    raise ValueError("Declare contributing source_row identities for merged rows")
                if any(
                    type(value) is not int or value < 0 or value not in original
                    for value in parent_ids
                ):
                    raise ValueError("A parent row is absent from the pinned input")
                parent_ids = list(dict.fromkeys(parent_ids))
                row_identity = row.get("source_row")
                if explicit is not None or row_identity in assigned:
                    row_identity, next_id = next_id, next_id + 1
                row["source_row"] = row_identity
                assigned.add(row_identity)
                originals = [original[value] for value in parent_ids]
                row = inherit_provenance(
                    row,
                    originals,
                    [{"row": value} for value in parent_ids],
                    group_by=source_groups,
                    inherited=inherited,
                )
                row.pop("human_reviewed", None)
                derived.append(row)
            derived = normalize_json_rows(derived)
            lineages[node["id"]] = derived
            values[node["id"]] = [
                {k: v for k, v in row.items() if k != "_overmind_provenance"} for row in derived
            ]
            os.chown(directory, 0, 0)
            directory.chmod(0o700)
        result["output_rows"] = len(produced)
        result["sample"] = produced[:3]
        if partition:
            result["partition"] = partition_assignment.assign(
                derived, len(derived), partition, destination
            )
            result["assignments_sha256"] = hashlib.sha256(
                (destination / "assignments.jsonl").read_bytes()
            ).hexdigest()
        public = root / "work" / "replays" / replay_id
        public.mkdir(parents=True, exist_ok=True)
        result["outputs"] = []
        for index in range(len(receipts)):
            target = public / f"output-{index}.jsonl"
            shutil.copyfile(destination / f"output-{index}.jsonl", target)
            target.chmod(0o444)
            result["outputs"].append(str(target.relative_to(root / "work")))
        if partition:
            shutil.copyfile(destination / "assignments.jsonl", public / "assignments.jsonl")
            (public / "assignments.jsonl").chmod(0o444)
            result["assignments_path"] = str(
                (public / "assignments.jsonl").relative_to(root / "work")
            )
        result["state"] = "completed"
    except (ValueError, OSError) as exc:
        result.update(state="failed", error=str(exc))
    finally:
        destination.chmod(0o700)
        destination.parent.chmod(0o700)
        (root / ".agent").chmod(0o700)
    session.save(receipt_path, result)
    return result
