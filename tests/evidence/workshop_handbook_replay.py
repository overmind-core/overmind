import argparse
import asyncio
import hashlib
import json
import os
import subprocess
import tempfile
import time
import tomllib
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PROJECT = "e18b29b5-915d-45a7-80cd-77ffe6559205"
RECIPE = "25b7e82f-af5a-478e-9ad1-ce0d9be78008"


async def main(options):
    with (
        Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
        / "overmind/connection.toml"
    ).open("rb") as source:
        connection = tomllib.load(source)
    base = connection["base-url"].rstrip("/")
    assert urlsplit(base).hostname in {"localhost", "127.0.0.1"}
    work = Path(tempfile.mkdtemp(prefix="workshop-handbook-"))
    report = {"success": False, "endpoint": base, "work": str(work), "calls": [], "runs": []}
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"OVERMIND_API_KEY", "OVERMIND_API_URL", "PYTHONPATH"}
    }

    def save():
        options.report.write_text(json.dumps(report, indent=2) + "\n")

    async def cli(*args):
        result = await asyncio.to_thread(
            subprocess.run,
            [options.cli, *map(str, args), "--json"],
            cwd=work,
            env=env,
            capture_output=True,
            text=True,
            timeout=600,
        )
        assert result.returncode == 0, result.stdout
        return json.loads(result.stdout)

    async def export(dataset, cell):
        path = work / f"{cell}.jsonl"
        if not path.exists():
            await cli("dataset", "export", dataset, "--cell", cell, "--output", path)
        with path.open() as source:
            return [json.loads(line) for line in source]

    try:
        async with (
            httpx.AsyncClient(headers={"X-Api-Key": connection["api-key"]}, timeout=90) as http,
            streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            projects = (await session.call_tool("list_projects", {})).structuredContent
            assert PROJECT in {p["id"] for p in projects["projects"]}
            report["catalog_sha256"] = projects["catalog_sha256"]

            async def call(tool_name, *, expect_error=None, **args):
                tick = time.monotonic()
                reply = await session.call_tool(tool_name, {"project_id": PROJECT, **args})
                report["calls"].append(
                    {
                        "tool": tool_name,
                        "seconds": round(time.monotonic() - tick, 4),
                        "wire_bytes": len(reply.model_dump_json().encode()),
                        "is_error": reply.isError,
                        "expected_error": expect_error,
                    }
                )
                result = reply.structuredContent
                if expect_error:
                    assert reply.isError and result["error"]["code"] == expect_error, result
                else:
                    assert not reply.isError, result
                return result

            detail = await call("inspect_dataset", dataset=options.dataset, cell_limit=1)
            source = detail["active"]
            rows = await export(options.dataset, source["id"])
            report["source"] = {"dataset": options.dataset, **source, "sources": detail["sources"]}
            if options.pdf:
                report["landings"] = []
                for index in range(2):
                    started = time.monotonic()
                    argv = [
                        "dataset",
                        "upload",
                        options.pdf,
                        "--project-id",
                        PROJECT,
                        "--intent",
                        "explore",
                        "--brief",
                        "Complex PDF extraction regression; preserve text evidence and original bytes. No training.",
                        "--request-key",
                        f"{options.run}-landing-{index}",
                    ]
                    receipt = await cli(*argv)
                    report["landing_pending"] = receipt
                    save()
                    observations = []
                    while time.monotonic() - started < 300:
                        job = await call("get_job", kind="dataset_run", id=receipt["id"])
                        observation = {
                            "seconds": round(time.monotonic() - started, 3),
                            "status": job["status"],
                            "landing": job["progress"].get("landing"),
                        }
                        observations.append(observation)
                        if job["status"] in {"idle", "error"}:
                            break
                        await asyncio.sleep(1)
                    assert job["status"] == "idle", job
                    seconds = round(time.monotonic() - started, 3)
                    landed = await call("inspect_dataset", dataset=receipt["id"], cell_limit=1)
                    repeated_rows = await export(receipt["id"], landed["active"]["id"])
                    assert repeated_rows == rows
                    assert (
                        landed["sources"][0]["extraction"]["native_text_recovery"][
                            "control_characters"
                        ]
                        == 57
                    )
                    assert (await cli(*argv))["transfer"]["id"] == receipt["transfer"]["id"]
                    assert (await call("inspect_dataset", dataset=receipt["id"], cell_limit=1))[
                        "cell_page"
                    ]["total"] == 1
                    report["landings"].append(
                        {
                            "dataset": receipt["id"],
                            "transfer": receipt["transfer"]["id"],
                            "seconds": seconds,
                            "active": landed["active"],
                            "sources": landed["sources"],
                            "observations": observations,
                            "identical_export": True,
                            "idempotent_retry": True,
                        }
                    )
                    save()
                    print(
                        json.dumps(
                            {
                                "landing": index,
                                "dataset": receipt["id"],
                                "seconds": seconds,
                                "identical_export": True,
                            }
                        ),
                        flush=True,
                    )
            expected = {}
            for row in rows:
                expected.setdefault(row["page"], []).append(row)
            assert set(expected) == set(range(1, 40)) - {2}
            assert len(rows) == len({row["source_row"] for row in rows})
            for page, anchor in [
                (3, "Product Teams Building AI Agents!"),
                (11, "Multi prompt attacks"),
                (12, "[INSERT SYSTEM PROMPT]"),
                (33, "Model Processing"),
            ]:
                assert anchor in "\n".join(row["text"] for row in expected[page]), (page, anchor)
            original = (await call("inspect_dataset_workbench", pipeline=RECIPE, limit=1))[
                "pipeline"
            ]
            package = work / "original.zip"
            await cli(
                "dataset",
                "pipeline-download",
                original["package"],
                "--project-id",
                PROJECT,
                "--output",
                package,
            )
            variant = work / "variant.zip"
            with zipfile.ZipFile(package) as prior, zipfile.ZipFile(variant, "w") as changed:
                for name in prior.namelist():
                    content = prior.read(name)
                    if name == "audit.py":
                        content = Path(__file__).with_name("handbook_review.py").read_bytes()
                    changed.writestr(name, content)
            uploaded = await cli("dataset", "pipeline-upload", variant, "--project-id", PROJECT)
            report["package_upload"] = uploaded
            adapted = await call(
                "save_dataset_pipeline",
                name="Handbook: retain native and OCR evidence",
                package=uploaded["id"],
                derived_from=RECIPE,
                request_key=options.run + "-adapt",
            )
            recipes = [("reuse", original), ("ocr-branch", adapted["pipeline"])]
            report["recipes"] = [recipe for _, recipe in recipes]
            for label, recipe in recipes:
                assert not recipe["flow"]["unconsumed_steps"]
                validation = await call(
                    "validate_dataset_pipeline",
                    pipeline=recipe["id"],
                    source_cell=source["id"],
                    source_fingerprint=source["fingerprint"],
                )
                report.setdefault("validation", {})[label] = validation["validation"]
                draft = await call(
                    "start_dataset",
                    name=f"Lakera {label} {options.run}",
                    brief="Verification of retained document transformation execution and evidence preservation. No training or inferred answers.",
                    intent="explore",
                )
                destination = draft["dataset"]["id"]
                for index, mode in enumerate(["preview", "publish", "publish"]):
                    arguments = {
                        "dataset": destination,
                        "pipeline": recipe["id"],
                        "source_cell": source["id"],
                        "source_fingerprint": source["fingerprint"],
                        "request_key": f"{options.run}-{label}-{index}",
                        "mode": mode,
                        "preview_rows": 3,
                    }
                    tick = time.monotonic()
                    run = (await call("run_dataset_pipeline", **arguments))["run"]
                    assert (await call("run_dataset_pipeline", **arguments))["run"]["id"] == run[
                        "id"
                    ]
                    while time.monotonic() - tick < 300:
                        job = await call("get_job", kind="dataset_pipeline", id=run["id"])
                        if job["status"] in {"completed", "failed", "cancelled"}:
                            break
                        await asyncio.sleep(job["details"].get("poll_after_seconds") or 3)
                    assert job["status"] == "completed", job
                    state = await call("inspect_dataset", dataset=destination, cell_limit=1)
                    if mode == "preview":
                        assert not state["cells"]
                    else:
                        steps = {step["step_id"]: step for step in job["progress"]["steps"]}
                        flagged = {
                            row["source_row"]
                            for row in rows
                            if label == "ocr-branch"
                            and row["_overmind_provenance"]["extraction"]["method"]
                            == "tesseract-ocr"
                        }
                        for name, members in (
                            ("review", flagged),
                            ("unflagged", {r["source_row"] for r in rows} - flagged),
                        ):
                            selected = await export(destination, steps[name]["output_cell"])
                            assert {r["source_row"] for r in selected} == members
                        output = await export(destination, steps["pages"]["output_cell"])
                        assert len(output) == len(expected)
                        for row in output:
                            members = sorted(expected[row["page"]], key=lambda r: r["source_row"])
                            assert row["text"] == "\n".join(member["text"] for member in members)
                            assert row["evidence_rows"] == len(members)
                            assert {
                                parent["row"] for parent in row["_overmind_provenance"]["parents"]
                            } == {member["source_row"] for member in members}
                        report.setdefault("outputs", {})[destination] = {
                            "cell": steps["pages"]["output_cell"],
                            "pages": len(output),
                            "content_sha256": hashlib.sha256(
                                json.dumps([(r["page"], r["text"]) for r in output]).encode()
                            ).hexdigest(),
                        }
                    measurement = {
                        "label": label,
                        "dataset": destination,
                        "mode": mode,
                        "run": run["id"],
                        "seconds": round(time.monotonic() - tick, 3),
                        "server_seconds": job["progress"].get("seconds"),
                        "steps": [
                            {
                                key: step.get(key)
                                for key in ("step_id", "input_rows", "output_rows", "output_cell")
                            }
                            for step in job["progress"]["steps"]
                        ],
                    }
                    report["runs"].append(measurement)
                    save()
                    print(json.dumps(measurement), flush=True)
                before = await call("inspect_dataset", dataset=destination, cell_limit=1)
                await call(
                    "run_dataset_pipeline",
                    **{
                        **arguments,
                        "source_fingerprint": "0" * 64,
                        "request_key": f"{options.run}-{label}-bad",
                    },
                    expect_error="source_conflict",
                )
                after = await call("inspect_dataset", dataset=destination, cell_limit=1)
                assert after["active"] == before["active"]
            final = await call("inspect_dataset", dataset=options.dataset, cell_limit=1)
            assert (
                final["active"] == source
                and final["cell_page"]["total"] == detail["cell_page"]["total"]
            )
            after_recipe = (await call("inspect_dataset_workbench", pipeline=RECIPE, limit=1))[
                "pipeline"
            ]
            assert after_recipe == original
            report["success"] = True
    except Exception as error:
        report["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--cli", default="/Users/tyleredwards/.local/bin/overmind")
    parser.add_argument("--pdf", type=Path)
    asyncio.run(main(parser.parse_args()))
