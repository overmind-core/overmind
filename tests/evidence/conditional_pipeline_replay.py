"""Local MCP + CLI conditional workflow; no paid services or browser dependencies."""

import asyncio
import hashlib
import json
import os
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from reusable_pipeline_replay import fixture

CLASSIFIER = """import json,sys
parameters=json.load(open(sys.argv[3]))
with open(sys.argv[1]) as src, open(sys.argv[2],'w') as dst:
 for line in src:
  row=json.loads(line)
  score=row['payload'].get('score')
  if score is None:
   route='missing'
  elif type(score) not in (int,float):
   route='invalid'
  else:
   if row['payload']['enabled'] and score >= parameters['threshold']:
    if score >= parameters['threshold']+5:
     route='priority'
    else:
     route='accept'
   else:
    route='reject'
  row['route']=route
  dst.write(json.dumps(row,ensure_ascii=False)+'\\n')
"""


def package_files(runtime):
    files = {"classify.py": CLASSIFIER}
    steps = [
        {
            "id": "classify",
            "input": "source",
            "name": "Classify boundary cases",
            "entrypoint": "classify.py",
            "input_schema": {"payload": "object"},
            "checks": {"preserve_rows": True},
            "condition": {
                "expression": "enabled and score >= threshold; nested priority threshold",
                "line": 12,
            },
        }
    ]
    for name, parent, expression in [
        ("accepted", "classify", "row['route'] in ('accept','priority')"),
        ("priority", "accepted", "row['route'] == 'priority'"),
        ("fallback", "classify", "row['route'] not in ('accept','priority')"),
    ]:
        files[f"{name}.py"] = (
            "import json,sys\nwith open(sys.argv[1]) as src, open(sys.argv[2],'w') as dst:\n"
            " for line in src:\n  row=json.loads(line)\n"
            f"  if {expression}:\n   dst.write(json.dumps(row,ensure_ascii=False)+'\\n')\n"
        )
        steps.append(
            {
                "id": name,
                "input": parent,
                "name": name,
                "entrypoint": f"{name}.py",
                "condition": {"expression": expression, "line": 5},
            }
        )
    files["manifest.json"] = json.dumps(
        {
            "version": 1,
            "runtime": runtime,
            "steps": steps,
            "parameters": {"threshold": "number"},
            "limits": {"scratch_mb": 512},
        }
    )
    return files


def records(count, field="payload"):
    scores = [-1, 0, 4, 5, 6, 10, None, False, 10, 5]
    for index in range(count):
        yield {
            "source_row": index,
            field: {"score": scores[index % 10], "enabled": index % 10 != 8},
            "text": "Résumé 🧪",
            "target": {"options": ["B", "A"], "probabilities": [0.35, 0.65]},
        }


async def main():
    repository = Path.cwd()
    user, project, key = await asyncio.to_thread(fixture)
    report = {
        "project": project,
        "transport": "local Streamable HTTP MCP + CLI bytes",
        "runs": [],
        "checks": [],
        "success": False,
    }
    with TemporaryDirectory(prefix="conditional-workshop-") as directory:
        root = Path(directory)
        environment = {
            **os.environ,
            "PYTHONPATH": str(repository / "overmind"),
            "OVERMIND_API_KEY": key,
            "OVERMIND_API_URL": "http://127.0.0.1:8000",
        }

        async def cli(*args, succeeds=True):
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "overmind",
                    "dataset",
                    *args,
                    *(["--project-id", project] if args[0] != "export" else []),
                    "--json",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert (result.returncode == 0) == succeeds, (
                result.stdout or "CLI returned no structured receipt"
            )
            return json.loads(result.stdout) if succeeds else None

        async def upload_package(name, files, succeeds=True):
            location = root / name
            location.mkdir()
            for filename, content in files.items():
                (location / filename).write_text(content)
            return await cli("pipeline-upload", str(location), succeeds=succeeds)

        async def upload_source(count, field="payload"):
            location = root / f"source-{count}-{field}.jsonl"
            with location.open("w") as target:
                for row in records(count, field):
                    target.write(json.dumps(row, ensure_ascii=False) + "\n")
            return await cli("upload", str(location), "--wait", "--intent", "explore")

        try:
            async with (
                httpx.AsyncClient(headers={"X-Api-Key": key}, timeout=60) as http,
                streamable_http_client("http://127.0.0.1:8000/api/mcp/", http_client=http) as (
                    read,
                    write,
                    _,
                ),
                ClientSession(read, write) as session,
            ):
                await session.initialize()

                async def call(tool_name, *, error=None, **arguments):
                    response = await session.call_tool(
                        tool_name,
                        {"project_id": project, **arguments}
                        if tool_name != "list_projects"
                        else arguments,
                    )
                    value = response.structuredContent
                    if error:
                        assert response.isError and value["error"]["code"] == error, value
                    else:
                        assert not response.isError, value
                    return value

                async def resource(uri, client=session):
                    response = await client.read_resource(uri)
                    return json.loads(response.contents[0].text)

                async def execute(
                    recipe,
                    dataset,
                    original,
                    request,
                    threshold=5,
                    mode="publish",
                    expected="completed",
                ):
                    args = {
                        "dataset": dataset["id"],
                        "pipeline": recipe["id"],
                        "source_cell": original["id"],
                        "source_fingerprint": original["fingerprint"],
                        "request_key": request,
                        "parameters": {"threshold": threshold},
                        "mode": mode,
                    }
                    receipt = await call("run_dataset_pipeline", **args)
                    repeat = await call("run_dataset_pipeline", **args)
                    assert receipt["job"]["id"] == repeat["job"]["id"]
                    start = time.monotonic()
                    last = None
                    while time.monotonic() - start < 300:
                        job = await call(
                            "get_job", kind="dataset_pipeline", id=receipt["job"]["id"]
                        )
                        state = (job["status"], job.get("progress", {}).get("stage"))
                        if state != last:
                            print(json.dumps({"experiment": request, "state": state}), flush=True)
                            last = state
                        if job["status"] in {"completed", "failed", "cancelled"}:
                            assert job["status"] == expected, job
                            report["runs"].append(
                                {
                                    "experiment": request,
                                    "id": job["id"],
                                    "status": job["status"],
                                    "seconds": round(time.monotonic() - start, 3),
                                    "rows": [
                                        item.get("output_rows")
                                        for item in job["details"]["result"]["steps"]
                                    ],
                                }
                            )
                            return job
                        await asyncio.sleep(2)
                    raise AssertionError("Conditional run exceeded 300 seconds")

                async def verify(
                    job, dataset, count, accepted=(3, 4, 5, 9), priority=(5,), field="payload"
                ):
                    all_ids = set(range(count))
                    accepted_ids = {n for n in all_ids if n % 10 in accepted}
                    expected = {
                        "classify": all_ids,
                        "accepted": accepted_ids,
                        "priority": {n for n in all_ids if n % 10 in priority},
                        "fallback": all_ids - accepted_ids,
                    }
                    for step in job["details"]["result"]["steps"]:
                        identifier = step["step_id"]
                        cell = step["output_cell"]
                        destination = root / f"{cell}.jsonl"
                        await cli(
                            "export",
                            dataset["id"],
                            "--cell",
                            cell,
                            "--format",
                            "jsonl",
                            "--output",
                            str(destination),
                        )
                        actual = [json.loads(line) for line in destination.read_text().splitlines()]
                        assert {row["source_row"] for row in actual} == expected[identifier]
                        assert len(actual) == len(expected[identifier]) == step["output_rows"]
                        for row in actual:
                            assert row["text"] == "Résumé 🧪"
                            assert row["target"] == {
                                "options": ["B", "A"],
                                "probabilities": [0.35, 0.65],
                            }
                            assert field in row
                        queried = await call(
                            "query_dataset",
                            dataset=dataset["id"],
                            cell=cell,
                            sql="SELECT count(*) AS total FROM t",
                        )
                        assert queried["rows"][0]["total"] == len(actual), queried
                    assert accepted_ids.isdisjoint(expected["fallback"])
                    assert accepted_ids | expected["fallback"] == all_ids
                    report["checks"].append(
                        {
                            "run": job["id"],
                            "exact_members": count,
                            "content_preserved": True,
                            "partition_verified_by_test": True,
                        }
                    )

                projects = await call("list_projects")
                assert any(item["id"] == project for item in projects["projects"])
                interface = await resource("overmind://interface/current")
                assert interface["contract_version"] == "5.2.0"
                catalogue = await session.list_tools()
                tool = next(
                    item for item in catalogue.tools if item.name == "inspect_dataset_workbench"
                )
                assert "pipeline" in tool.inputSchema["properties"]
                prompt = await session.get_prompt(
                    "author-dataset-transformation",
                    {
                        "dataset": "synthetic routing cases",
                        "task": "Retain all boundary cases and split accepted, priority and fallback outputs",
                    },
                )
                assert prompt.messages
                workbench = await call("inspect_dataset_workbench")
                assert workbench["runner"]["status"] in {"ready", "executing"}
                files = package_files(workbench["runner"]["images"][0])
                uploaded = await upload_package("original", files)
                recipe = (
                    await call(
                        "save_dataset_pipeline",
                        name="Conditional routing",
                        request_key="original",
                        package=uploaded["id"],
                    )
                )["pipeline"]
                first = await upload_source(10000)
                original = (await call("inspect_dataset", dataset=first["id"]))["active"]
                assert original["rows"] == 10000
                validation = await call(
                    "validate_dataset_pipeline",
                    pipeline=recipe["id"],
                    source_cell=original["id"],
                    source_fingerprint=original["fingerprint"],
                )
                assert validation["validation"]["valid"]
                preview = await execute(recipe, first, original, "preview", mode="preview")
                assert [item["output_rows"] for item in preview["details"]["result"]["steps"]] == [
                    100,
                    40,
                    10,
                    60,
                ]
                assert (await call("inspect_dataset", dataset=first["id"]))["active"][
                    "id"
                ] == original["id"]
                full = await execute(recipe, first, original, "boundary-10000")
                await verify(full, first, 10000)
                empty = await execute(recipe, first, original, "empty-branches", threshold=11)
                await verify(empty, first, 10000, accepted=set(), priority=set())

                async with (
                    httpx.AsyncClient(headers={"X-Api-Key": key}, timeout=60) as fresh_http,
                    streamable_http_client(
                        "http://127.0.0.1:8000/api/mcp/", http_client=fresh_http
                    ) as (fresh_read, fresh_write, _),
                    ClientSession(fresh_read, fresh_write) as fresh,
                ):
                    await fresh.initialize()
                    response = await fresh.call_tool(
                        "inspect_dataset_workbench",
                        {"project_id": project, "pipeline": recipe["id"]},
                    )
                    assert not response.isError and response.structuredContent["pipeline"] == recipe
                    retained = await resource(
                        f"overmind://dataset-pipelines/{recipe['id']}?project_id={project}", fresh
                    )
                    package_uri = str(retained["package_resource"]["uri"])
                    inventory = await resource(package_uri, fresh)
                    retrieved = {}
                    for item in inventory["inventory"]:
                        chunks, offset = [], 0
                        while offset is not None:
                            result = await resource(
                                f"{package_uri}&file={item['path']}&offset={offset}&limit=173",
                                fresh,
                            )
                            chunks.append(result["file"]["content"])
                            offset = result["file"]["next_offset"]
                        retrieved[item["path"]] = "".join(chunks)
                        assert (
                            hashlib.sha256(retrieved[item["path"]].encode()).hexdigest()
                            == item["sha256"]
                        )
                    assert retrieved == files
                archive = root / "retrieved.zip"
                await cli("pipeline-download", uploaded["id"], "--output", str(archive))
                with zipfile.ZipFile(archive) as bundle:
                    assert {
                        name: bundle.read(name).decode() for name in bundle.namelist()
                    } == retrieved
                report["checks"].append(
                    {"fresh_client_paginated_retrieval": True, "download_matches": True}
                )

                variant_files = {
                    name: content.replace("payload", "observation")
                    for name, content in retrieved.items()
                }
                manifest = json.loads(variant_files["manifest.json"])
                steps = manifest["steps"]
                manifest["steps"] = [steps[0], steps[3], steps[1], steps[2]]
                variant_files["manifest.json"] = json.dumps(manifest)
                variant_package = await upload_package("adapted", variant_files)
                variant = (
                    await call(
                        "save_dataset_pipeline",
                        name="Adapted observation with reordered branches",
                        request_key="variant",
                        derived_from=recipe["id"],
                        package=variant_package["id"],
                    )
                )["pipeline"]
                assert (
                    variant["derived_from"] == recipe["id"]
                    and variant["pipeline_id"] != recipe["pipeline_id"]
                )
                assert variant["flow"]["output"] == "priority"
                other = await upload_source(100, "observation")
                other_original = (await call("inspect_dataset", dataset=other["id"]))["active"]
                mismatch = await call(
                    "validate_dataset_pipeline",
                    pipeline=recipe["id"],
                    source_cell=other_original["id"],
                    source_fingerprint=other_original["fingerprint"],
                )
                assert not mismatch["validation"]["valid"]
                adapted = await execute(variant, other, other_original, "adapted-reordered")
                await verify(adapted, other, 100, field="observation")

                revised_files = {
                    **retrieved,
                    "classify.py": retrieved["classify.py"].replace(
                        "score >= parameters['threshold']:", "score > parameters['threshold']:"
                    ),
                }
                revised_package = await upload_package("revised", revised_files)
                revised = (
                    await call(
                        "save_dataset_pipeline",
                        name="Strict boundary",
                        request_key="strict",
                        pipeline=recipe["pipeline_id"],
                        expected_revision=1,
                        package=revised_package["id"],
                    )
                )["pipeline"]
                assert revised["revision"] == 2
                await call(
                    "save_dataset_pipeline",
                    error="revision_conflict",
                    name="Stale",
                    request_key="stale",
                    pipeline=recipe["pipeline_id"],
                    expected_revision=1,
                    package=uploaded["id"],
                )
                strict = await execute(revised, first, original, "strict-boundary")
                await verify(strict, first, 10000, accepted={4, 5})
                old = await call("inspect_dataset_workbench", pipeline=recipe["id"], limit=1)
                assert (
                    old["pipeline"] == recipe
                    and old["pipelines"][0]["id"] == revised["id"]
                    and old["pipeline_page"]["total"] == 2
                )
                large = await upload_source(100000)
                large_original = (await call("inspect_dataset", dataset=large["id"]))["active"]
                replay = await execute(
                    recipe, large, large_original, "original-after-revision-100000"
                )
                await verify(replay, large, 100000)

                failed_files = {
                    **retrieved,
                    "priority.py": retrieved["priority.py"]
                    + "raise RuntimeError('synthetic downstream failure')\n",
                }
                failed_package = await upload_package("failure", failed_files)
                failed_recipe = (
                    await call(
                        "save_dataset_pipeline",
                        name="Fail after earlier steps",
                        request_key="failure",
                        derived_from=recipe["id"],
                        package=failed_package["id"],
                    )
                )["pipeline"]
                before = await call("inspect_dataset", dataset=first["id"])
                await execute(
                    failed_recipe, first, original, "downstream-failure", expected="failed"
                )
                after = await call("inspect_dataset", dataset=first["id"])
                assert (
                    after["active"]["id"] == before["active"]["id"]
                    and after["cells"] == before["cells"]
                )
                for name, change in [
                    ("duplicate", {"id": "classify"}),
                    ("forward", {"input": "fallback"}),
                    ("unknown", {"input": "missing"}),
                    ("join", {"input": ["classify", "fallback"]}),
                    ("bad-line", {"condition": {"expression": "x", "line": 999}}),
                ]:
                    broken = json.loads(files["manifest.json"])
                    broken["steps"][1].update(change)
                    await upload_package(
                        name, {**files, "manifest.json": json.dumps(broken)}, succeeds=False
                    )
                report["checks"].append(
                    {
                        "invalid_graphs_rejected": 5,
                        "failed_run_preserves_cells": True,
                        "revision_conflict_distinct": True,
                    }
                )
                report["success"] = True
                print(json.dumps(report), flush=True)
        finally:
            cleanup = f'''from overbae.models import APIToken,DatasetPipelineBinding,Project
DatasetPipelineBinding.objects.filter(project_id="{uuid.UUID(project)}").update(enabled=False)
APIToken.objects.filter(user_id={int(user)}).delete()
Project.objects.filter(pk="{uuid.UUID(project)}").update(is_active=False)'''
            await asyncio.to_thread(
                subprocess.run,
                [
                    "docker",
                    "compose",
                    "exec",
                    "-T",
                    "api",
                    "python",
                    "manage.py",
                    "shell",
                    "--no-imports",
                    "-c",
                    cleanup,
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            (repository / "tests/evidence/conditional-workshop-live-results.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )


if __name__ == "__main__":
    asyncio.run(main())
