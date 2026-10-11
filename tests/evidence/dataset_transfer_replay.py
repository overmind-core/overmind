"""Real local CLI -> interrupted transfer -> MCP transformation -> exact export."""

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import time
import tomllib
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

INTERRUPTED_UPLOAD = """
import json, sys
from pathlib import Path
import requests
from overmind.dataset_cmd import upload_file
from overmind.transfer_connection import TransferError, resolve_transfer_connection
original = requests.Session.request
def interrupted(self, method, url, **kwargs):
    response = original(self, method, url, **kwargs)
    if method == 'PUT' and '/dataset-transfers/' in url and response.ok:
        raise requests.ConnectionError('controlled client disconnection after acknowledged chunk')
    return response
requests.Session.request = interrupted
key, base = resolve_transfer_connection('', '', None)
try:
    result = upload_file(Path(sys.argv[1]), project_id=sys.argv[2], api_key=key, api_url=base,
                        json_rows_field='pairs', request_key=sys.argv[3])
    print(json.dumps({'already_published': result}))
except TransferError as exc:
    print(json.dumps({'interruption': exc.record()}))
"""


async def replay(arguments):
    directory = arguments.directory.resolve()
    source_path = arguments.file.resolve()
    raw = source_path.read_bytes()
    original = json.loads(raw)
    expected = original["pairs"]
    assert len(expected) == 10000
    sha256 = hashlib.sha256(raw).hexdigest()
    executable = str(arguments.cli) if arguments.cli else shutil.which("overmind")
    assert executable
    installed_python = Path(executable).resolve().parent / "python"
    environment = dict(os.environ)
    for key in ("OVERMIND_API_KEY", "OVERMIND_API_URL", "PYTHONPATH"):
        environment.pop(key, None)
    environment["OVERMIND_ANALYTICS_ENABLED"] = "false"
    profile_path = (
        Path(environment.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "overmind/connection.toml"
    )
    profile = tomllib.loads(profile_path.read_text())
    assert profile["base-url"] == "http://localhost:8000"
    assert not (directory / "overmind.toml").exists()
    started = time.monotonic()
    evidence = {
        "input_bytes": len(raw),
        "input_sha256": sha256,
        "input_rows": len(expected),
        "cli": executable,
        "cwd": str(directory),
        "credential_flags": False,
        "credential_environment": False,
        "browser_calls": 0,
        "paid_jobs": 0,
    }

    async def execute(*command):
        process = await asyncio.create_subprocess_exec(
            *map(str, command),
            cwd=directory,
            env=environment,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 300)
        assert process.returncode == 0, stdout.decode() + stderr.decode()
        return json.loads(stdout)

    async with (
        httpx.AsyncClient(
            headers={"X-Api-Key": profile["api-key"]}, timeout=60, trust_env=False
        ) as http,
        streamable_http_client(profile["base-url"] + "/api/mcp/", http_client=http) as (
            read,
            write,
            _,
        ),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        async def call(tool_name, **values):
            response = await session.call_tool(tool_name, values)
            assert not response.isError, response.content
            return response.structuredContent

        projects = (await call("list_projects", limit=100))["projects"]
        project = next(p for p in projects if p["name"] == "financial-services")
        project_id = project["id"]
        evidence["project"] = project
        evidence["readiness"] = await execute(
            executable, "connection", "check", "--project-id", project_id, "--json"
        )
        if arguments.resume_transfer:
            transfer_id = arguments.resume_transfer
            evidence["continued_saved_receipt"] = True
        else:
            interrupted = await execute(
                installed_python,
                "-c",
                INTERRUPTED_UPLOAD,
                source_path,
                project_id,
                arguments.request_key,
            )
            assert "interruption" in interrupted, (
                "Use a fresh --request-key to repeat the interruption scenario."
            )
            transfer_id = interrupted["interruption"]["transfer"]["id"]
        receipt = await call(
            "get_job", project_id=project_id, kind="dataset_transfer", id=transfer_id
        )
        assert 0 < receipt["progress"]["bytes_received"] <= len(raw)
        received = receipt["progress"]["bytes_received"]
        evidence[
            "resume_from_bytes" if arguments.resume_transfer else "interrupted_after_bytes"
        ] = received
        print(
            json.dumps(
                {
                    "stage": "resuming" if arguments.resume_transfer else "interrupted",
                    "transfer_id": transfer_id,
                    "received": received,
                }
            ),
            flush=True,
        )
        upload_command = (
            executable,
            "dataset",
            "upload",
            source_path,
            "--project-id",
            project_id,
            "--json-rows-field",
            "pairs",
            "--request-key",
            arguments.request_key,
            "--json",
        )
        uploaded, replayed = await asyncio.gather(
            execute(*upload_command), execute(*upload_command)
        )
        assert uploaded["transfer"]["id"] == transfer_id
        assert replayed["id"] == uploaded["id"] and replayed["transfer"]["id"] == transfer_id
        dataset_id = uploaded["id"]
        evidence.update(
            dataset_id=dataset_id,
            transfer_id=transfer_id,
            replay_same_dataset=True,
            concurrent_publication_recovered=True,
            publication_seconds=round(time.monotonic() - started, 3),
        )

        async def wait_job(kind, ident, successful):
            deadline = time.monotonic() + 300
            while True:
                job = await call("get_job", project_id=project_id, kind=kind, id=ident)
                if job["status"] in successful:
                    return job
                assert job["status"] not in {"failed", "error", "cancelled"}, job
                assert time.monotonic() < deadline, job
                await asyncio.sleep(2)

        await wait_job("dataset_run", dataset_id, {"idle"})
        source = await call("inspect_dataset", project_id=project_id, dataset=dataset_id)
        cell = min(source["cells"], key=lambda candidate: candidate["position"])
        assert cell["rows"] == 10000
        evidence.update(
            source_cell=cell["id"],
            source_fingerprint=cell["fingerprint"],
            source_ready_seconds=round(time.monotonic() - started, 3),
        )
        print(
            json.dumps({"stage": "source_ready", "dataset_id": dataset_id, "rows": cell["rows"]}),
            flush=True,
        )
        key = arguments.request_key + "-projection"
        recipe = await call(
            "save_dataset_pipeline",
            project_id=project_id,
            dataset=dataset_id,
            name="Transfer verification: preserve pair fields",
            request_key=key,
            steps=[{"operation": "select", "columns": ["left", "right", "judgement"]}],
        )
        run = await call(
            "run_dataset_pipeline",
            project_id=project_id,
            dataset=dataset_id,
            pipeline=recipe["pipeline"]["id"],
            source_cell=cell["id"],
            source_fingerprint=cell["fingerprint"],
            request_key=key,
        )
        await wait_job("dataset_pipeline", run["job"]["id"], {"completed"})
        transformed = await call("inspect_dataset", project_id=project_id, dataset=dataset_id)
        output_cell = transformed["active"]
        assert output_cell["id"] != cell["id"] and output_cell["rows"] == 10000
        workbench = await call(
            "inspect_dataset_workbench", project_id=project_id, dataset=dataset_id
        )
        assert len(workbench["runs"]) == 1
        for label, selected_cell in (("source", cell), ("transformed", output_cell)):
            output = directory / f"{dataset_id}-{label}.jsonl"
            exported = await execute(
                executable,
                "dataset",
                "export",
                dataset_id,
                "--cell",
                selected_cell["id"],
                "--output",
                output,
                "--json",
            )
            assert exported["cell"] == selected_cell["id"]
            rows = [json.loads(line) for line in output.read_text().splitlines()]
            assert len(rows) == len(expected)
            assert [
                {key: row[key] for key in ("left", "right", "judgement")} for row in rows
            ] == expected
            assert len({row["source_row"] for row in rows}) == len(expected)
            evidence[label + "_export"] = exported
        assert hashlib.sha256(source_path.read_bytes()).hexdigest() == sha256
        evidence.update(
            output_cell=output_cell["id"],
            output_fingerprint=output_cell["fingerprint"],
            pipeline_run=run["job"]["id"],
            verified_rows=10000,
            nested_values_preserved=True,
            original_file_unchanged=True,
            total_seconds=round(time.monotonic() - started, 3),
        )
        print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--request-key", required=True)
    parser.add_argument("--cli", type=Path)
    parser.add_argument("--resume-transfer")
    asyncio.run(replay(parser.parse_args()))
