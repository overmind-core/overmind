import argparse
import asyncio
import hashlib
import json
import os
import statistics
import subprocess
import tempfile
import time
import tomllib
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

PROJECT = "e18b29b5-915d-45a7-80cd-77ffe6559205"


async def main(options):
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    with (config / "overmind/connection.toml").open("rb") as stream:
        connection = tomllib.load(stream)
    base = connection["base-url"].rstrip("/")
    assert urlsplit(base).hostname in {"localhost", "127.0.0.1"}
    report = {"run": options.run, "endpoint": base, "cases": [], "calls": [], "success": False}
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in {"OVERMIND_API_KEY", "OVERMIND_API_URL", "PYTHONPATH"}
    }
    work = options.directory

    def save():
        options.report.write_text(json.dumps(report, indent=2) + "\n")

    async def cli(*args):
        result = await asyncio.to_thread(
            subprocess.run,
            [options.cli, *map(str, args), "--json"],
            cwd=work,
            env=env,
            text=True,
            capture_output=True,
            timeout=300,
        )
        body = json.loads(result.stdout)
        assert result.returncode == 0, body
        return body

    try:
        async with (
            httpx.AsyncClient(headers={"X-Api-Key": connection["api-key"]}, timeout=90) as http,
            streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
            ClientSession(read, write) as session,
        ):
            await session.initialize()
            projects = await session.call_tool("list_projects", {})
            assert PROJECT in {p["id"] for p in projects.structuredContent["projects"]}

            async def call(name, **arguments):
                tick = time.monotonic()
                result = await session.call_tool(name, {"project_id": PROJECT, **arguments})
                report["calls"].append(
                    {
                        "tool": name,
                        "seconds": round(time.monotonic() - tick, 4),
                        "wire_bytes": len(result.model_dump_json().encode()),
                    }
                )
                assert not result.isError, result.structuredContent
                return result.structuredContent

            async def upload(path, key, *, dataset=None, rejected=False):
                tick = time.monotonic()
                args = [
                    "dataset",
                    "upload",
                    path,
                    "--project-id",
                    PROJECT,
                    "--request-key",
                    f"{options.run}-{key}",
                ]
                args += ["--dataset", dataset] if dataset else ["--intent", "explore"]
                receipt = await cli(*args)
                dataset = receipt["id"]
                while time.monotonic() - tick < 180:
                    job = await call("get_job", kind="dataset_run", id=dataset)
                    if job["status"] in {"idle", "error"}:
                        break
                    await asyncio.sleep(0.3)
                assert job["status"] == ("error" if rejected else "idle"), job
                detail = await call(
                    "inspect_dataset", dataset=dataset, cell_limit=1, source_limit=1
                )
                result = {
                    "file": path.name,
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "dataset": dataset,
                    "transfer": receipt["transfer"]["id"],
                    "cell": detail["active"],
                    "sources": detail["source_page"],
                    "seconds": round(time.monotonic() - tick, 3),
                    "error": job.get("job_error"),
                }
                report["cases"].append(result)
                save()
                return result, detail, args

            async def exported(dataset, cell):
                path = Path(tempfile.mkdtemp(dir=work)) / "rows.jsonl"
                await cli("dataset", "export", dataset, "--cell", cell, "--output", path)
                return [json.loads(line) for line in path.read_text().splitlines()]

            initial, before, _ = await upload(work / "pdfs/native-1.pdf", "recovery-source")
            dataset = initial["dataset"]
            original = await exported(dataset, initial["cell"]["id"])
            failed, after, _ = await upload(
                work / "pdfs/corrupt.pdf", "recovery-failure", dataset=dataset, rejected=True
            )
            assert after["active"]["id"] == before["active"]["id"]
            assert after["cell_page"]["total"] == 1
            assert await exported(dataset, after["active"]["id"]) == original
            restored, after, args = await upload(
                work / "pdfs/native-10.pdf", "recovery-valid", dataset=dataset
            )
            assert after["active"]["rows"] == 22
            assert after["cell_page"]["total"] == 2
            repeat = await cli(*args)
            assert repeat["transfer"]["id"] == restored["transfer"]
            assert await exported(dataset, before["active"]["id"]) == original
            report["recovery"] = {
                "passed": True,
                "dataset": dataset,
                "retained_cell": before["active"]["id"],
            }
            save()

            fixtures = json.loads((work / "pdfs/manifest.json").read_text())
            batch = [item for item in fixtures if item["kind"] == "batch"]
            semaphore = asyncio.Semaphore(4)

            async def concurrent(item):
                async with semaphore:
                    landed, _, _ = await upload(
                        work / "pdfs" / item["filename"], "parallel-" + item["filename"]
                    )
                    rows = await exported(landed["dataset"], landed["cell"]["id"])
                    text = "\n".join(row["text"] for row in rows)
                    if item["kind"] == "batch":
                        assert len(rows) == 2 and item["marker"] in text
                    else:
                        assert {row["page"] for row in rows} == set(range(1, item["pages"] + 1))
                        assert "Returns are accepted for thirty days." in text
                    return landed

            tick = time.monotonic()
            load = batch + [
                item
                for item in fixtures
                if item["filename"] in {"scanned-25.pdf", "mixed-30.pdf", "near-byte-limit.pdf"}
            ]
            tasks = [asyncio.create_task(concurrent(item)) for item in load]
            probes = []
            while not all(task.done() for task in tasks):
                start = time.monotonic()
                await call(
                    "query_dataset", dataset=dataset, sql="SELECT count(*) AS n FROM t", limit=1
                )
                probes.append(round(time.monotonic() - start, 4))
                await asyncio.sleep(0.3)
            await asyncio.gather(*tasks)
            report["parallel"] = {
                "files": len(load),
                "concurrency": 4,
                "seconds": round(time.monotonic() - tick, 3),
                "probe_seconds": probes,
            }
            save()

            for index, item in enumerate(batch):
                added, detail, _ = await upload(
                    work / "pdfs" / item["filename"],
                    "quantity-" + item["filename"],
                    dataset=dataset,
                )
                assert added["cell"]["rows"] == 22 + 2 * (index + 1)
            sources, cells = [], []
            offset = 0
            while True:
                page = await call(
                    "inspect_dataset",
                    dataset=dataset,
                    source_limit=5,
                    source_offset=offset,
                    cell_limit=1,
                )
                sources.extend(page["sources"])
                cursor = page["source_page"]["next_cursor"]
                if cursor is None:
                    break
                offset = int(cursor)
            offset = 0
            while True:
                page = await call(
                    "inspect_dataset",
                    dataset=dataset,
                    cell_limit=5,
                    cell_offset=offset,
                    source_limit=1,
                )
                cells.extend(page["cells"])
                cursor = page["cell_page"]["next_cursor"]
                if cursor is None:
                    break
                offset = int(cursor)
            assert len(sources) == len({source["id"] for source in sources}) == 27
            assert len(cells) == len({cell["id"] for cell in cells}) == 27
            expected = [
                work / "pdfs/native-1.pdf",
                work / "pdfs/native-10.pdf",
                *(work / "pdfs" / item["filename"] for item in batch),
            ]
            assert {source["sha256"] for source in sources} == {
                hashlib.sha256(path.read_bytes()).hexdigest() for path in expected
            }
            output = await exported(dataset, page["active"]["id"])
            assert len(output) == 72 and output[:2] == original
            assert len({row["source_row"] for row in output}) == 72
            assert all(row["_overmind_provenance"]["evidence"] for row in output)
            report["quantity"] = {
                "passed": True,
                "dataset": dataset,
                "rows": 72,
                "sources": 27,
                "cells": 27,
            }
            report["success"] = True
    except Exception as error:
        report["error"] = {"type": type(error).__name__, "detail": str(error)[:2000]}
        raise
    finally:
        report["latency"] = {}
        for name in {item["tool"] for item in report["calls"]}:
            values = sorted(item["seconds"] for item in report["calls"] if item["tool"] == name)
            report["latency"][name] = {
                "count": len(values),
                "median": statistics.median(values),
                "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
                "max": max(values),
            }
        save()
        print(
            json.dumps(
                {
                    key: report.get(key)
                    for key in ("success", "recovery", "parallel", "quantity", "error", "latency")
                }
            )
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--cli", required=True)
    asyncio.run(main(parser.parse_args()))
