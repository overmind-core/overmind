"""Run with uv against local Compose/API; only Workshop tools are called."""

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


def fixture():
    code = """import json,uuid
from overbae.models import APIToken,Project,ProjectMembership,User
suffix=uuid.uuid4().hex
user=User.objects.create_user(email=f"pipeline-replay-{suffix}@example.test")
project=Project.objects.create(name="Pipeline verification",slug=f"pipeline-{suffix}")
ProjectMembership.objects.create(user=user,project=project)
key,_=APIToken.create_for_user(user)
print(json.dumps({"user":str(user.pk),"project":str(project.pk),"key":key}))"""
    result = subprocess.run(
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
            code,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    value = json.loads(result.stdout)
    return value["user"], value["project"], value["key"]


async def main():
    user, project, key = await asyncio.to_thread(fixture)
    report = {
        "project": project,
        "transport": "MCP SDK Streamable HTTP + local CLI bytes",
        "results": [],
    }
    repository = Path.cwd()
    with TemporaryDirectory(prefix="workshop-replay-") as directory:
        root = Path(directory)
        environment = {
            **os.environ,
            "PYTHONPATH": str(repository / "overmind"),
            "OVERMIND_API_KEY": key,
            "OVERMIND_API_URL": "http://127.0.0.1:8000",
        }

        async def cli(*args):
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-m",
                    "overmind",
                    "dataset",
                    *args,
                    "--project-id",
                    project,
                    "--json",
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert result.returncode == 0, result.stdout or "CLI failed before returning a receipt"
            return json.loads(result.stdout)

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

                async def call(tool_name, **arguments):
                    assert tool_name in {
                        "list_projects",
                        "inspect_dataset",
                        "inspect_dataset_workbench",
                        "save_dataset_pipeline",
                        "validate_dataset_pipeline",
                        "run_dataset_pipeline",
                        "get_job",
                        "query_dataset",
                        "save_dataset_pipeline_binding",
                        "run_dataset_pipeline_binding",
                        "set_dataset_pipeline_binding_state",
                    }
                    value = await session.call_tool(
                        tool_name,
                        {"project_id": project, **arguments}
                        if tool_name != "list_projects"
                        else arguments,
                    )
                    assert not value.isError, value.structuredContent
                    return value.structuredContent

                async def wait(run):
                    started, last = time.monotonic(), None
                    while time.monotonic() - started < 120:
                        value = await call("get_job", kind="dataset_pipeline", id=run)
                        state = (value["status"], value.get("progress", {}).get("stage"))
                        if state != last:
                            print(json.dumps({"run": run, "state": state}), flush=True)
                            last = state
                        if value["status"] in {"completed", "failed", "cancelled"}:
                            assert value["status"] == "completed", value
                            report["results"].append(
                                {
                                    "run": run,
                                    "seconds": round(time.monotonic() - started, 3),
                                    "state": "completed",
                                }
                            )
                            return value
                        await asyncio.sleep(2)
                    raise AssertionError("Run did not finish within the bounded test window")

                projects = await call("list_projects")
                assert any(item["id"] == project for item in projects["projects"])
                workbench = await call("inspect_dataset_workbench")
                assert workbench["runner"]["status"] in {"ready", "executing"}, workbench["runner"]
                runtime = workbench["runner"]["images"][0]
                source_file = root / "source.jsonl"
                source_file.write_text(
                    "".join(json.dumps({"value": n}) + "\n" for n in range(10000))
                )
                first = await cli("upload", str(source_file), "--wait", "--intent", "explore")
                other_file = root / "other.jsonl"
                other_file.write_text('{"value":7}\n{"value":9}\n')
                second = await cli("upload", str(other_file), "--wait", "--intent", "explore")
                package = root / "package"
                package.mkdir()
                (package / "manifest.json").write_text(
                    json.dumps(
                        {
                            "version": 1,
                            "runtime": runtime,
                            "steps": [
                                {
                                    "name": "Double values",
                                    "entrypoint": "step.py",
                                    "input_schema": {"value": "integer"},
                                    "output_schema": {"value": "integer"},
                                    "checks": {"preserve_rows": True},
                                }
                            ],
                        }
                    )
                )
                (package / "step.py").write_text(
                    "import json,sys\nwith open(sys.argv[1]) as src, open(sys.argv[2],'w') as dst:\n for line in src:\n  row=json.loads(line)\n  row['value']*=2\n  dst.write(json.dumps(row)+'\\n')\n"
                )
                retained = await cli("pipeline-upload", str(package))
                recipe = (
                    await call(
                        "save_dataset_pipeline",
                        name="Reusable double",
                        request_key="double",
                        package=retained["id"],
                    )
                )["pipeline"]
                originals = {}
                for destination in [first, second]:
                    original = (await call("inspect_dataset", dataset=destination["id"]))["active"]
                    originals[destination["id"]] = original
                    validation = await call(
                        "validate_dataset_pipeline",
                        pipeline=recipe["id"],
                        source_cell=original["id"],
                        source_fingerprint=original["fingerprint"],
                    )
                    assert validation["validation"]["valid"]
                    for mode in ["preview", "publish"]:
                        receipt = await call(
                            "run_dataset_pipeline",
                            dataset=destination["id"],
                            pipeline=recipe["id"],
                            source_cell=original["id"],
                            source_fingerprint=original["fingerprint"],
                            request_key=mode,
                            mode=mode,
                        )
                        job = await wait(receipt["job"]["id"])
                        assert (job["details"]["output_cell"] is None) == (mode == "preview")
                branch_package = root / "branches"
                branch_package.mkdir()
                branch_steps = []
                for name, parity in [("even", 0), ("odd", 1)]:
                    (branch_package / f"{name}.py").write_text(
                        "import json,sys\nwith open(sys.argv[1]) as src, open(sys.argv[2],'w') as dst:\n for line in src:\n  row=json.loads(line)\n"
                        f"  if row['value'] % 2 == {parity}:\n   dst.write(json.dumps(row)+'\\n')\n"
                    )
                    branch_steps.append(
                        {
                            "id": name,
                            "input": "source",
                            "name": name,
                            "entrypoint": f"{name}.py",
                            "condition": {"expression": f"value % 2 == {parity}", "line": 5},
                        }
                    )
                (branch_package / "manifest.json").write_text(
                    json.dumps({"version": 1, "runtime": runtime, "steps": branch_steps})
                )
                branch_upload = await cli("pipeline-upload", str(branch_package))
                branch_recipe = (
                    await call(
                        "save_dataset_pipeline",
                        name="Explicit parity split",
                        request_key="parity",
                        package=branch_upload["id"],
                    )
                )["pipeline"]
                assert [edge["source"] for edge in branch_recipe["flow"]["edges"]] == [
                    "source",
                    "source",
                ]
                original = originals[first["id"]]
                branched = await call(
                    "run_dataset_pipeline",
                    dataset=first["id"],
                    pipeline=branch_recipe["id"],
                    source_cell=original["id"],
                    source_fingerprint=original["fingerprint"],
                    request_key="branches",
                )
                branch_result = await wait(branched["job"]["id"])
                steps = branch_result["details"]["result"]["steps"]
                assert [step["output_rows"] for step in steps] == [5000, 5000]
                assert [step["input_cells"] for step in steps] == [
                    [original["id"]],
                    [original["id"]],
                ]
                report["agent_authored_branches"] = {
                    "rows": [5000, 5000],
                    "structure_retained": True,
                    "source_pinned": True,
                }
                binding = (
                    await call(
                        "save_dataset_pipeline_binding",
                        dataset=second["id"],
                        source_dataset=first["id"],
                        pipeline=recipe["id"],
                        request_key="binding",
                        trigger="ingestion",
                        interval_seconds=10,
                    )
                )["binding"]
                assert not binding["enabled"]
                bound = await call("run_dataset_pipeline_binding", binding=binding["id"])
                await wait(bound["job"]["id"])
                unchanged = await call("run_dataset_pipeline_binding", binding=binding["id"])
                assert unchanged["run"] is None
                await call(
                    "set_dataset_pipeline_binding_state",
                    binding=binding["id"],
                    expected_version=binding["version"],
                    enabled=True,
                )
                current = (await call("inspect_dataset", dataset=first["id"]))["active"]
                upstream = await call(
                    "run_dataset_pipeline",
                    dataset=first["id"],
                    pipeline=recipe["id"],
                    source_cell=current["id"],
                    source_fingerprint=current["fingerprint"],
                    request_key="new-source-snapshot",
                )
                await wait(upstream["job"]["id"])
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    catalog = await call("inspect_dataset_workbench", dataset=second["id"])
                    automatic = next(
                        (
                            run
                            for run in catalog["runs"]
                            if run["binding"] == binding["id"] and run["id"] != bound["job"]["id"]
                        ),
                        None,
                    )
                    if automatic:
                        await wait(automatic["id"])
                        report["automatic_ingestion"] = (
                            "changed source published without a client run request"
                        )
                        break
                    await asyncio.sleep(2)
                else:
                    raise AssertionError("Enabled binding did not process changed source")
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
            (repository / "tests/evidence/reusable-workshop-live-results.json").write_text(
                json.dumps(report, indent=2) + "\n"
            )


if __name__ == "__main__":
    asyncio.run(main())
