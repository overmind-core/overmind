import argparse
import asyncio
import collections
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

ROOT = Path(__file__).resolve().parents[2]
FINANCE = "e18b29b5-915d-45a7-80cd-77ffe6559205"
KYC = "43d35b0c-dd4b-4d45-b167-2ab657123702"
CASES = [
    (
        "pairs",
        FINANCE,
        "dece0a89-ab26-4293-aa3a-b292d3a278c9",
        "c13a1cd1-b095-4c7d-b1fd-c4d298a748ac",
        KYC,
        10000,
    ),
    (
        "documents",
        FINANCE,
        "a14e5398-f0a4-4e97-b80c-8575d6c2b318",
        "96b430a0-088b-4c81-b76b-10ec3f484450",
        KYC,
        320,
    ),
    (
        "tabular",
        FINANCE,
        "7def7ef9-1ece-4335-ab31-0cf0df18705c",
        "b7aa0c70-da36-4f5e-af46-583e5bfcdff5",
        None,
        891,
    ),
    (
        "chat",
        FINANCE,
        "425a415c-7cc8-4175-83cc-049abb5cd6c7",
        "8922f0e7-a480-4aa4-aa3b-4483ae691d65",
        None,
        1000,
    ),
]


def rows(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream]


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def package(kind, runtime, location):
    location.mkdir()
    scripts = Path(__file__).with_name("workshop_steps")
    shutil.copyfile(scripts / "common.py", location / "common.py")
    steps = []

    def step(identity, parent, name, *, preserve=True, condition=None):
        if condition:
            script = (
                "import sys\nfrom common import read, write\n"
                f"write((row for row in read(sys.argv[1]) if {condition}), sys.argv[2])\n"
            )
        else:
            script = (scripts / kind / f"{identity}.py").read_text()
        (location / f"{identity}.py").write_text(script)
        value = {
            "id": identity,
            "input": parent,
            "name": name,
            "entrypoint": f"{identity}.py",
            "output_schema": {"source_row": "integer"},
            "checks": {},
        }
        if preserve:
            value["checks"] = {"preserve_rows": True, "min_rows": 1}
        if condition:
            value["condition"] = {"expression": condition, "line": 3}
        steps.append(value)

    step("audit", "source", "Inspect source and retain review evidence")
    parent = "audit"
    if kind == "pairs":
        steps[0]["input_schema"] = {"left": "object", "right": "object", "judgement": "string"}
        step("features", "audit", "Separate identity shortcuts from model evidence")
        parent = "features"
    elif kind == "documents":
        steps[0]["input_schema"] = {"text": "string", "page": "integer"}
    elif kind == "tabular":
        steps[0]["input_schema"] = {"Survived": "integer", "Age": "any"}
    else:
        steps[0]["input_schema"] = {"messages": "any"}
    step("review", parent, "Rows requiring review", preserve=False, condition="row['needs_review']")
    step(
        "unflagged",
        parent,
        "Rows without measured flags",
        preserve=False,
        condition="not row['needs_review']",
    )
    if kind == "documents":
        step("pages", parent, "Assemble page evidence without inventing targets", preserve=False)
        steps[-1]["output_schema"] = {"_overmind_parent_rows": "array", "text": "string"}
    else:
        step("messages", parent, "Prepare model inputs; retain all observations")
    steps[-1].pop("input")
    steps[-1]["inputs"] = ["review", "unflagged"]
    (location / "manifest.json").write_text(
        json.dumps(
            {
                "version": 1,
                "runtime": runtime,
                "steps": steps,
                "limits": {"seconds": 600, "scratch_mb": 1024, "memory_mb": 1024, "cpus": 1},
            }
        )
    )
    return steps


async def main(args):
    config = (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "overmind/connection.toml"
    )
    with config.open("rb") as stream:
        connection = tomllib.load(stream)
    base = connection["base-url"].rstrip("/")
    assert urlsplit(base).hostname in {"localhost", "127.0.0.1"}, "Local acceptance only"
    report = {
        "run": args.run,
        "endpoint": base,
        "cases": [],
        "errors": [],
        "success": False,
        "coverage": "Actual local MCP and installed CLI; deterministic replay, not independent fresh-agent conversations",
    }
    report_path = ROOT / "tests/evidence" / f"workshop-real-{args.run}.json"
    work = Path(tempfile.mkdtemp(prefix=f"workshop-real-{args.run}-"))
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OVERMIND_API_KEY", "OVERMIND_API_URL", "PYTHONPATH"}
    }

    async def cli(*argv):
        start = time.monotonic()
        completed = await asyncio.to_thread(
            subprocess.run,
            [args.cli, *map(str, argv)],
            cwd=work,
            env=env,
            text=True,
            capture_output=True,
            timeout=300,
        )
        assert completed.returncode == 0, completed.stdout or "CLI failed without a JSON receipt"
        value = json.loads(completed.stdout)
        return value, round(time.monotonic() - start, 3)

    try:
        async with (
            httpx.AsyncClient(headers={"X-Api-Key": connection["api-key"]}, timeout=90) as http,
            streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()

            async def call(tool_name, project=None, **kwargs):
                result = await session.call_tool(
                    tool_name, {**({"project_id": project} if project else {}), **kwargs}
                )
                value = result.structuredContent
                assert not result.isError, value
                return value

            projects = await call("list_projects", limit=100)
            assert FINANCE in {item["id"] for item in projects["projects"]}
            interface = json.loads(
                (await session.read_resource("overmind://interface/current")).contents[0].text
            )
            report["interface"] = {
                key: interface.get(key) for key in ("contract_version", "catalog_sha256")
            }
            runner = (await call("inspect_dataset_workbench", FINANCE, limit=1))["runner"]
            assert runner["status"] in {"ready", "executing"}, runner

            cases = (
                CASES
                if not args.security_source
                else [
                    (
                        "chat",
                        "76d44e4f-ad38-4769-acee-33b105a233e2",
                        "d3d6ccc9-80ed-47a0-8168-f309fe6e42f3",
                        "5564b4e1-0efb-4340-8b86-2b802d752403",
                        "c9451ee0-9646-40cd-a05e-ba9627b13b11",
                        900,
                    )
                ]
            )
            report["source_origin"] = (
                "stored synthetic SOC examples"
                if args.security_source
                else "existing user-supplied datasets"
            )
            for kind, project, original_dataset, original_cell, capability, count in cases:
                if args.only and kind not in args.only:
                    continue
                outcome = {
                    "kind": kind,
                    "original_dataset": original_dataset,
                    "original_cell": original_cell,
                    "source_rows": count,
                    "runs": [],
                }
                report["cases"].append(outcome)
                source_path = work / f"{kind}.jsonl"
                exported, seconds = await cli(
                    "dataset",
                    "export",
                    original_dataset,
                    "--cell",
                    original_cell,
                    "--output",
                    source_path,
                    "--json",
                )
                original = rows(source_path)
                assert len(original) == count
                outcome["export"] = exported
                outcome["export_seconds"] = seconds
                if capability:
                    await session.read_resource(
                        f"overmind://capabilities/{capability}?project_id={project}"
                    )
                location = work / f"{kind}-package"
                steps = package(kind, runner["images"][0], location)
                uploaded, _ = await cli(
                    "dataset", "pipeline-upload", location, "--project-id", project, "--json"
                )
                outcome["package"] = uploaded["id"]
                for item in uploaded["inventory"]:
                    content = ""
                    offset = 0
                    while offset is not None:
                        resource = await session.read_resource(
                            f"overmind://dataset-pipeline-packages/{uploaded['id']}?project_id={project}&file={item['path']}&offset={offset}&limit=8000"
                        )
                        page = json.loads(resource.contents[0].text)["file"]
                        content += page["content"]
                        offset = page["next_offset"]
                    assert content == (location / item["path"]).read_text()
                    assert hashlib.sha256(content.encode()).hexdigest() == item["sha256"]
                recipe = (
                    await call(
                        "save_dataset_pipeline",
                        project,
                        name=f"Acceptance {kind}: evidence and review",
                        package=uploaded["id"],
                        request_key=f"{args.run}-{kind}-recipe",
                    )
                )["pipeline"]
                assert len(recipe["flow"]["nodes"]) == len(steps)
                assert not recipe["flow"]["unconsumed_steps"]
                retained = await call(
                    "inspect_dataset_workbench", project, pipeline=recipe["id"], limit=1
                )
                assert retained["pipeline"]["fingerprint"] == recipe["fingerprint"]
                outcome["recipe"] = recipe["id"]

                for repeat in range(args.repeats):
                    tick = time.monotonic()
                    item = {"repeat": repeat + 1}
                    outcome["runs"].append(item)
                    draft = await call(
                        "start_dataset",
                        project,
                        name=f"Acceptance {args.run} {kind} {repeat + 1}",
                        brief=f"Regression of {kind}: retain source evidence, separate review branches and publish each preparation step. No paid operations. Task suitability remains unmeasured.",
                        intent="explore" if kind == "documents" else "train",
                        **({"capability": capability} if capability else {}),
                    )
                    input_path = source_path
                    source_options = []
                    if kind == "pairs" and repeat == 0:
                        supplied = Path("/Users/tyleredwards/Downloads/sample_10000.json")
                        if supplied.exists():
                            assert (
                                hashlib.sha256(supplied.read_bytes()).hexdigest()
                                == "fe2f701f6034d3adaa586ef747d3c3855ca10fcbac1de97fa3dd7e487058c521"
                            )
                            input_path = supplied
                            source_options = ["--json-rows-field", "pairs"]
                    if kind == "tabular":
                        input_path = work / f"{kind}-{repeat}.csv"
                        await cli(
                            "dataset",
                            "export",
                            original_dataset,
                            "--cell",
                            original_cell,
                            "--format",
                            "csv",
                            "--output",
                            input_path,
                            "--json",
                        )
                    item["upload_format"] = input_path.suffix
                    argv = [
                        str(input_path) if word == "FILE" else word
                        for word in draft["upload"]["argv"]
                    ][1:] + source_options
                    landed, upload_seconds = await cli(*argv, "--wait")
                    retried, _ = await cli(*argv, "--wait")
                    assert retried["id"] == landed["id"]
                    item.update(dataset=landed["id"], upload_seconds=upload_seconds)
                    inspected = await call(
                        "inspect_dataset", project, dataset=landed["id"], cell_limit=1
                    )
                    source = inspected["active"]
                    assert source["rows"] == count, source
                    assert inspected["cells"][0]["title"] == "Source"
                    assert inspected["cell_page"]["total"] == 1
                    if capability:
                        assert inspected["capability"]["id"] == capability
                    validated = await call(
                        "validate_dataset_pipeline",
                        project,
                        pipeline=recipe["id"],
                        source_cell=source["id"],
                        source_fingerprint=source["fingerprint"],
                    )
                    assert validated["validation"]["valid"], validated

                    async def execute(
                        mode,
                        landed=landed,
                        recipe=recipe,
                        source=source,
                        kind=kind,
                        repeat=repeat,
                        project=project,
                        item=item,
                        steps=steps,
                    ):
                        arguments = dict(
                            dataset=landed["id"],
                            pipeline=recipe["id"],
                            source_cell=source["id"],
                            source_fingerprint=source["fingerprint"],
                            request_key=f"{args.run}-{kind}-{repeat}-{mode}",
                            mode=mode,
                            preview_rows=3,
                        )
                        receipt = await call("run_dataset_pipeline", project, **arguments)
                        recovered = await call("run_dataset_pipeline", project, **arguments)
                        assert recovered["run"]["id"] == receipt["run"]["id"]
                        assert receipt["run"]["poll_after_seconds"]
                        deadline = time.monotonic() + 300
                        observed = []
                        while time.monotonic() < deadline:
                            job = await call(
                                "get_job", project, kind="dataset_pipeline", id=receipt["run"]["id"]
                            )
                            state = job["status"]
                            observed.append({"state": state, "stage": job["progress"].get("stage")})
                            if state in {"completed", "failed", "cancelled"}:
                                break
                            await asyncio.sleep(job["details"].get("poll_after_seconds") or 5)
                        assert job["status"] == "completed", job
                        assert job["completed_at"] and job["details"]["poll_after_seconds"] is None
                        for declaration, measured in zip(
                            steps, job["progress"]["steps"], strict=True
                        ):
                            checks = measured["check_results"]
                            assert not checks["failed"]
                            if declaration["checks"].get("preserve_rows"):
                                assert checks["passed"]["preserve_rows"] is True
                                target = "deferred" if mode == "preview" else "passed"
                                assert checks[target]["min_rows"] == 1
                        item[mode] = {
                            "id": receipt["run"]["id"],
                            "observed": observed,
                            "steps": [
                                {
                                    key: step.get(key)
                                    for key in (
                                        "step_id",
                                        "input_step",
                                        "input_rows",
                                        "output_rows",
                                        "output_cell",
                                        "seconds",
                                        "check_results",
                                    )
                                }
                                for step in job["progress"]["steps"]
                            ],
                        }
                        return job

                    await execute("preview")
                    after_preview = await call(
                        "inspect_dataset", project, dataset=landed["id"], cell_limit=1
                    )
                    assert after_preview["cell_page"]["total"] == 1
                    await execute("publish")
                    # Page explicitly: response byte budgets can shorten even a requested large page.
                    cells, offset = [], 0
                    while True:
                        inspected = await call(
                            "inspect_dataset",
                            project,
                            dataset=landed["id"],
                            cell_limit=5,
                            cell_offset=offset,
                        )
                        cells.extend(inspected["cells"])
                        cursor = inspected["cell_page"]["next_cursor"]
                        if cursor is None:
                            break
                        offset = int(cursor)
                    assert len(cells) == 1 + len(steps), [c["title"] for c in cells]
                    item["cells"] = [
                        {key: cell[key] for key in ("id", "title", "rows")} for cell in cells
                    ]
                    assert [cell["title"] for cell in cells[1:]] == [step["name"] for step in steps]
                    for cell, step in zip(cells[1:], steps, strict=True):
                        assert cell["transformation"]["execution"] == "isolated_container"
                        assert cell["transformation"]["pipeline"] == recipe["id"]
                        assert cell["transformation"]["package"] == uploaded["id"]
                        assert cell["transformation"]["entrypoint"] == step["entrypoint"]
                        assert cell["script"] == (location / step["entrypoint"]).read_text()
                    final_path = work / f"{kind}-{repeat}-output.jsonl"
                    await cli("dataset", "export", landed["id"], "--output", final_path, "--json")
                    result = rows(final_path)
                    if kind == "documents":
                        assert len(result) == len(
                            {
                                (r.get("_overmind_document_id") or r["source_name"], r["page"])
                                for r in original
                            }
                        )
                        assert sum(row["evidence_rows"] for row in result) == count
                        for row in result:
                            expected = "\n".join(
                                r["text"] for r in original if r["page"] == row["page"]
                            )
                            assert row["text"] == expected
                        assert not any("messages" in row for row in result)
                    else:
                        assert len(result) == count
                        assert [row["source_row"] for row in result] == [
                            row["source_row"] for row in original
                        ]
                        if kind == "chat":
                            for before, after in zip(original, result, strict=True):
                                before_messages = before["messages"]
                                if isinstance(before_messages, str):
                                    before_messages = json.loads(before_messages)
                                assert before_messages == after["messages"]
                        elif kind == "pairs":
                            assert collections.Counter(
                                row["judgement"] for row in result
                            ) == collections.Counter(row["judgement"] for row in original)
                            groups = {}
                            for before, after in zip(original, result, strict=True):
                                assert after["messages"][-1]["content"] == before["judgement"]
                                for side in ("left", "right"):
                                    assert set(after["features"][side]) == {
                                        "caption",
                                        "schema",
                                        "properties",
                                    }
                                    assert after["features"][side] == {
                                        field: before[side][field]
                                        for field in ("caption", "schema", "properties")
                                    }
                                    for entity in [
                                        before[side].get("id"),
                                        *before[side].get("referents", []),
                                    ]:
                                        if entity:
                                            assert (
                                                groups.setdefault(entity, after["entity_group"])
                                                == after["entity_group"]
                                            )
                        else:
                            for before, after in zip(original, result, strict=True):
                                assert after["messages"][-1]["content"] == str(before["Survived"])
                                assert "Survived" not in json.loads(after["messages"][1]["content"])
                    by_title = {cell["title"]: cell for cell in cells}
                    review_cell = by_title["Rows requiring review"]
                    unflagged_cell = by_title["Rows without measured flags"]
                    assert review_cell["rows"] + unflagged_cell["rows"] == count
                    branch_members = []
                    for name, cell in (("review", review_cell), ("unflagged", unflagged_cell)):
                        target = work / f"{kind}-{repeat}-{name}.jsonl"
                        await cli(
                            "dataset",
                            "export",
                            landed["id"],
                            "--cell",
                            cell["id"],
                            "--output",
                            target,
                            "--json",
                        )
                        members = rows(target)
                        assert all(
                            bool(row["needs_review"]) == (name == "review") for row in members
                        )
                        branch_members.append({row["source_row"] for row in members})
                    assert not branch_members[0] & branch_members[1]
                    assert branch_members[0] | branch_members[1] == {
                        row["source_row"] for row in original
                    }
                    item.update(
                        seconds=round(time.monotonic() - tick, 3),
                        output_rows=len(result),
                        output_sha256=hashlib.sha256(final_path.read_bytes()).hexdigest(),
                        coverage="Every row; labels/messages, identity groups, branch membership/disjointness and complete source coverage",
                    )
                    print(
                        json.dumps(
                            {
                                "kind": kind,
                                "repeat": repeat + 1,
                                "dataset": landed["id"],
                                "seconds": item["seconds"],
                                "cells": len(cells),
                                "rows": len(result),
                            }
                        ),
                        flush=True,
                    )
                    report_path.write_text(json.dumps(report, indent=2))
                if args.repair and kind == "pairs":
                    await call("update_dataset", project, dataset=original_dataset, capability=KYC)
                    args_for_repair = dict(
                        dataset=original_dataset,
                        pipeline=recipe["id"],
                        source_cell=original_cell,
                        source_fingerprint=exported["fingerprint"],
                        request_key=f"{args.run}-repair-original",
                    )
                    receipt = await call("run_dataset_pipeline", project, **args_for_repair)
                    outcome["repair_run"] = receipt["run"]["id"]
                    # The receipt remains inspectable if this client disconnects while repairing the original.
            report["success"] = True
    except Exception as error:
        report["errors"].append({"type": type(error).__name__, "detail": str(error)[:6000]})
        raise
    finally:
        report["local_artifacts"] = str(work)
        report_path.write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument(
        "--cli",
        required=True,
        help="Absolute installed CLI path; uv's project environment may shadow it",
    )
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--only", nargs="*")
    parser.add_argument("--repair", action="store_true")
    parser.add_argument("--security-source", action="store_true")
    asyncio.run(main(parser.parse_args()))
