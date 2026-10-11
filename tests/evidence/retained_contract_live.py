import argparse
import hashlib
import itertools
import json
import subprocess
import tempfile
import time
from pathlib import Path

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PROJECT = "e18b29b5-915d-45a7-80cd-77ffe6559205"
HISTORICAL = "033236f1-4301-4772-8397-ef756144cac5"


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, f"Command failed: {args[:3]} (exit {result.returncode})"
    return json.loads(result.stdout)


async def verify(source, output):
    directory = Path(tempfile.mkdtemp(prefix="workshop-retained-contract-"))
    package_dir = Path(__file__).parent / "kyc_retained_pipeline"
    with source.open() as handle:
        records = [
            {key: row[key] for key in ("tokens", "kyc_risk_bucket")}
            for row in map(json.loads, itertools.islice(handle, 200))
        ]
    assert len(records) == 200
    input_file = directory / "real-kyc-prefix.jsonl"
    input_file.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in records))
    transport = command("/usr/local/bin/codex", "mcp", "get", "overmind", "--json")["transport"]
    assert transport["url"] == "http://localhost:8000/api/mcp/"
    evidence = {"endpoint": transport["url"], "project": PROJECT, "source_rows": len(records)}
    suffix = directory.name
    async with (
        httpx.AsyncClient(headers=transport.get("http_headers", {}), timeout=60) as client,
        streamable_http_client(transport["url"], http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        async def call(tool, *, error=False, **arguments):
            result = await session.call_tool(tool, {"project_id": PROJECT, **arguments})
            assert bool(result.isError) == error, (tool, result.structuredContent)
            return result.structuredContent

        projects = await session.call_tool("list_projects", {})
        assert projects.structuredContent["connection"]["mcp_url"] == transport["url"]
        assert PROJECT in {p["id"] for p in projects.structuredContent["projects"]}
        interface = json.loads(
            (await session.read_resource("overmind://interface/current")).contents[0].text
        )
        assert interface["contract_version"] == "6.0"
        tools = await session.list_tools()
        schema = next(t.inputSchema for t in tools.tools if t.name == "save_dataset_pipeline")
        assert "package" in schema["required"] and "steps" not in schema["properties"]
        evidence["contract"] = interface["contract_version"]
        evidence["catalog_sha256"] = projects.structuredContent["catalog_sha256"]
        failure = await call(
            "save_dataset_pipeline",
            error=True,
            name="Rejected inline recipe",
            request_key=suffix + "-negative",
            steps=[{"operation": "select", "columns": ["tokens"]}],
        )
        assert "pipeline-upload" in str(failure)
        evidence["package_free_rejection"] = failure["error"]
        historical = await call("inspect_dataset_workbench", pipeline=HISTORICAL, limit=1)
        assert not historical["pipeline"]["executable"]
        rejected = await call("validate_dataset_pipeline", error=True, pipeline=HISTORICAL)
        assert rejected["error"]["code"] == "pipeline_package_required"
        evidence["historical_read_only"] = True

        draft = await call(
            "start_dataset",
            name="Retained code contract · real KYC validation",
            intent="train",
            brief="Verify retained staged Python execution on 200 existing KYC records; preserve supplied labels, do not train.",
        )
        dataset = draft["dataset"]["id"]
        evidence["dataset"] = dataset
        print(json.dumps({"stage": "uploading", "dataset": dataset}), flush=True)
        await anyio.to_thread.run_sync(
            lambda: command(
                "overmind",
                "dataset",
                "upload",
                str(input_file),
                "--project-id",
                PROJECT,
                "--dataset",
                dataset,
                "--request-key",
                suffix,
                "--json",
                "--wait",
            )
        )
        inspected = await call("inspect_dataset", dataset=dataset)
        source_cell = inspected["cells"][0]
        assert source_cell["rows"] == 200
        evidence["source_cell"] = source_cell["id"]
        uploaded = await anyio.to_thread.run_sync(
            lambda: command(
                "overmind",
                "dataset",
                "pipeline-upload",
                str(package_dir),
                "--project-id",
                PROJECT,
                "--json",
            )
        )
        package = uploaded["id"]
        evidence["package"] = package
        downloaded = directory / "retained.zip"
        await anyio.to_thread.run_sync(
            lambda: command(
                "overmind",
                "dataset",
                "pipeline-download",
                package,
                "--project-id",
                PROJECT,
                "--output",
                str(downloaded),
                "--json",
            )
        )
        assert hashlib.sha256(downloaded.read_bytes()).hexdigest() == uploaded["sha256"]
        saved = await call(
            "save_dataset_pipeline",
            name="Retained KYC stages · contract validation",
            request_key=suffix,
            package=package,
        )
        recipe = saved["pipeline"]
        evidence["pipeline"] = recipe["id"]
        assert recipe["executable"] and len(recipe["steps"]) == 3
        validation = await call(
            "validate_dataset_pipeline",
            pipeline=recipe["id"],
            source_cell=source_cell["id"],
            source_fingerprint=source_cell["fingerprint"],
        )
        assert validation["validation"]["valid"]
        evidence["runs"] = []
        for mode in ("preview", "publish"):
            arguments = dict(
                dataset=dataset,
                pipeline=recipe["id"],
                source_cell=source_cell["id"],
                source_fingerprint=source_cell["fingerprint"],
                request_key=suffix + "-" + mode,
                mode=mode,
                preview_rows=25,
            )
            receipt = await call("run_dataset_pipeline", **arguments)
            run_id = receipt["run"]["id"]
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                job = await call("get_job", kind="dataset_pipeline", id=run_id)
                if job["status"] in {"completed", "failed", "cancelled"}:
                    break
                await anyio.sleep(min(job.get("poll_after_seconds") or 3, 10))
            assert job["status"] == "completed", job
            details = job["details"]
            assert details["execution"] == "isolated_container"
            count = 25 if mode == "preview" else 200
            assert [step["output_rows"] for step in details["result"]["steps"]] == [count] * 3
            assert all(step["exit_code"] == 0 for step in details["result"]["steps"])
            assert (await call("run_dataset_pipeline", **arguments))["run"]["id"] == run_id
            inspected = await call("inspect_dataset", dataset=dataset)
            assert len(inspected["cells"]) == (1 if mode == "preview" else 4)
            evidence["runs"].append(
                {
                    "id": run_id,
                    "mode": mode,
                    "rows_per_step": [count] * 3,
                    "seconds": details["result"]["seconds"],
                    "output_cell": details["output_cell"],
                }
            )
            print(json.dumps({"stage": mode, "run": run_id, "status": "completed"}), flush=True)
        output_cell = details["output_cell"]
        for cell in inspected["cells"][1:]:
            assert cell["transformation"]["package"] == package
            assert cell["transformation"]["execution"] == "isolated_container"
        exported = directory / "published.jsonl"
        await anyio.to_thread.run_sync(
            lambda: command(
                "overmind",
                "dataset",
                "export",
                dataset,
                "--cell",
                output_cell,
                "--output",
                str(exported),
                "--format",
                "jsonl",
                "--json",
            )
        )
        actual = [json.loads(line) for line in exported.read_text().splitlines()]
        assert len(actual) == len(records)
        for index, (row, original) in enumerate(zip(actual, records, strict=True)):
            assert row["source_row"] == index
            assert (
                row["question"] == original["tokens"]
                and row["answer"] == original["kyc_risk_bucket"]
            )
            assert row["messages"] == [
                {"role": "user", "content": original["tokens"]},
                {"role": "assistant", "content": original["kyc_risk_bucket"]},
            ]
        evidence["all_rows_verified"] = len(actual)
        evidence["package_checksum_verified"] = True
    output.write_text(json.dumps(evidence, indent=2) + "\n")
    print(json.dumps({"result": "passed", "evidence": str(output), "artifacts": str(directory)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    anyio.run(verify, args.source, args.output)
