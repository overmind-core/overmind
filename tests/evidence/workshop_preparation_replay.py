"""Replay local MCP + CLI + isolated-runner preparation without training or inference."""

import argparse
import asyncio
import json
import subprocess
import tempfile
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from overmind.transfer_connection import resolve_transfer_connection


async def main(args):
    key, base = resolve_transfer_connection("", "", None)
    assert base == "http://localhost:8000", "The saved account must target local Overmind."
    report = {"endpoint": base, "project": args.project, "runs": [], "checks": []}
    root = Path(args.output).resolve().parent
    root.mkdir(parents=True, exist_ok=True)
    prefix = f"preparation-{int(time.time())}"
    async with (
        httpx.AsyncClient(headers={"X-Api-Key": key}, timeout=120) as http,
        streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        projects = (await session.call_tool("list_projects", {"limit": 100})).structuredContent
        assert projects["connection"]["mcp_url"] == base + "/api/mcp/"
        assert args.project in {p["id"] for p in projects["projects"]}

        async def call(tool_name, **arguments):
            response = await session.call_tool(tool_name, {"project_id": args.project, **arguments})
            assert not response.isError, response.structuredContent
            return response.structuredContent

        async def cli(*arguments):
            result = await asyncio.to_thread(
                subprocess.run,
                ["overmind", "dataset", *arguments, "--project-id", args.project, "--json"],
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert result.returncode == 0, result.stdout
            return json.loads(result.stdout)

        async def wait(kind, identity, expected="completed"):
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                job = await call("get_job", kind=kind, id=identity)
                if job["status"] in {"completed", "failed", "cancelled"}:
                    report["runs"].append(
                        {
                            "kind": kind,
                            "id": identity,
                            "status": job["status"],
                            "progress": job.get("progress"),
                            "details": job.get("details"),
                        }
                    )
                    assert job["status"] == expected, job
                    Path(args.output).write_text(json.dumps(report, indent=2, default=str))
                    print(json.dumps({"run": identity, "status": job["status"]}), flush=True)
                    return job
                await asyncio.sleep(2)
            raise AssertionError(f"{kind} did not finish within 300 seconds: {identity}")

        with tempfile.TemporaryDirectory(prefix=prefix) as directory:
            work = Path(directory)
            package = work / "pipeline"
            package.mkdir()
            code = Path(args.titanic_package, "transform.py").read_text()
            manifest = json.loads(Path(args.titanic_package, "manifest.json").read_text())
            step = manifest["steps"][0]
            step.update(consumer="decision_train", batch_rows=200, checks={"preserve_rows": True})
            original = (await call("inspect_dataset", dataset=args.source_dataset))["cells"]
            source = next(c for c in original if c["id"] == args.source_cell)
            destination = await call(
                "start_dataset",
                name="Titanic preparation verification",
                brief="Verify corrected retained transformations and native holdout lineage using the existing Titanic source. No training.",
                intent="train",
            )
            dataset = (
                destination["dataset"]["id"]
                if isinstance(destination["dataset"], dict)
                else destination["dataset"]
            )
            report["dataset"] = dataset
            report["source"] = {
                "dataset": args.source_dataset,
                "cell": source["id"],
                "fingerprint": source["fingerprint"],
            }

            async def save(code, request, parent=None):
                (package / "manifest.json").write_text(json.dumps(manifest))
                (package / "transform.py").write_text(code)
                uploaded = await cli("pipeline-upload", str(package))
                package_id = uploaded.get("id") or uploaded.get("package", {}).get("id")
                assert package_id, uploaded
                saved = await call(
                    "save_dataset_pipeline",
                    dataset=dataset,
                    name="Titanic survival preparation",
                    request_key=prefix + request,
                    package=package_id,
                    **(
                        {"pipeline": parent["pipeline_id"], "expected_revision": parent["revision"]}
                        if parent
                        else {}
                    ),
                )
                return saved["pipeline"]

            async def run(recipe, request, mode="publish", expected="completed"):
                arguments = dict(
                    dataset=dataset,
                    pipeline=recipe["id"],
                    source_cell=source["id"],
                    source_fingerprint=source["fingerprint"],
                    request_key=prefix + request,
                    mode=mode,
                )
                launched = await call("run_dataset_pipeline", **arguments)
                repeated = await call("run_dataset_pipeline", **arguments)
                assert launched["job"]["id"] == repeated["job"]["id"]
                return await wait("dataset_pipeline", launched["job"]["id"], expected)

            invalid = await save(code.replace("[0.0, 1.0]", "[0.4, 0.4]"), "-invalid")
            bad = await run(invalid, "-preview-invalid", mode="preview", expected="failed")
            assert "normalized" in bad["details"]["error"]
            first = await save(code, "-first", invalid)
            await run(first, "-preview", mode="preview")
            published = await run(first, "-publish")
            earlier = published["details"]["output_cell"]
            corrected_code = code.replace(
                '        ("Passenger ID", row.get("PassengerId")),\n', ""
            ).replace('        ("Name", row.get("Name")),\n', "")
            corrected = await save(corrected_code, "-corrected", first)
            final = await run(corrected, "-corrected")
            output = final["details"]["output_cell"]
            process = (await call("inspect_dataset_workbench", dataset=dataset))["preparation"]
            assert {n["cell"] for n in process["nodes"]} == {source["id"], output}
            assert earlier not in {n["cell"] for n in process["nodes"]}
            assert final["progress"]["steps"][0]["batches"]["completed"] == 5
            report["checks"].extend(
                [
                    "invalid nested target fails preview",
                    "stable request key does not replay",
                    "891 real rows published through five isolated batches",
                    "corrected process replaces prior cells",
                ]
            )
            partition = await call(
                "create_data_partition",
                source_cell=output,
                name="Titanic verified holdouts",
                request_key=prefix + "-partition",
                recipe={
                    "fractions": {
                        "train": 0.7,
                        "development": 0.15,
                        "calibration": 0.05,
                        "final": 0.1,
                    },
                    "seed": 17,
                    "stratify_by": "survived_label",
                },
            )
            report["partition_receipt"] = partition
            job_id = partition.get("workflow", {}).get("id")
            assert job_id, partition
            await wait("data_partition", job_id)
            process = (await call("inspect_dataset_workbench", dataset=dataset))["preparation"]
            assert {n["role"] for n in process["nodes"] if n["role"]} == {
                "train",
                "development",
                "calibration",
                "final",
            }
            assert len(process["nodes"]) == 6 and len(process["edges"]) == 5
            report["preparation"] = process
            report["checks"].append("all four partitions expose source and retained transformation")
            response = await http.get(base + f"/api/datasets/{dataset}/preparation/")
            response.raise_for_status()
            assert {n["cell"]["id"] for n in response.json()["nodes"]} == {
                n["cell"] for n in process["nodes"]
            }
            report["checks"].append("REST and MCP process graphs agree")
            report["url"] = f"http://localhost:5173/datasets/{dataset}?projectId={args.project}"
        Path(args.output).write_text(json.dumps(report, indent=2, default=str))
        print(
            json.dumps({"report": args.output, "checks": report["checks"], "url": report["url"]}),
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True)
    parser.add_argument("--source-dataset", required=True)
    parser.add_argument("--source-cell", required=True)
    parser.add_argument("--titanic-package", required=True)
    parser.add_argument("--output", required=True)
    asyncio.run(main(parser.parse_args()))
