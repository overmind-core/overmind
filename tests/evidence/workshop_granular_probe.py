import argparse
import asyncio
import csv
import gzip
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
import pyarrow as pa
import pyarrow.parquet as pq
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from PIL import Image, ImageDraw, ImageFont

PROJECT = "e18b29b5-915d-45a7-80cd-77ffe6559205"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fixtures(directory):
    directory.mkdir(exist_ok=True)
    cases = []
    for name, values in (
        ("mixed-scalars", [None, 0, False, 1, True, "", "0", "False"]),
        ("large-integers", [2**63 - 1, 2**63, 10**40, -(10**40)]),
        ("mixed-numeric-precision", [2**53 + 1, 1.25]),
        ("nullable-integers", [None, 2**53 + 1]),
        ("late-type-change", [0] * 10001 + [False, "0"]),
        (
            "nested-unicode",
            [
                {"target": [0.2, 0.8], "name": "東京 café 🧪"},
                [False, 0, "", None],
                '{"literal":true}',
            ],
        ),
    ):
        records = [{"row_id": i, "value": value} for i, value in enumerate(values)]
        path = directory / f"{name}.jsonl"
        path.write_text("".join(canonical(row) + "\n" for row in records))
        cases.append({"name": name, "path": path, "rows": records})
    records = [
        {"id": "001", "note": '東京, "quoted"\nnext line', "n": 1},
        {"id": "002", "note": "", "n": 2},
    ]
    for suffix, delimiter in (("csv", ","), ("tsv", "\t")):
        path = directory / f"quoted.{suffix}"
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(records[0]), delimiter=delimiter)
            writer.writeheader()
            writer.writerows(records)
        cases.append({"name": suffix, "path": path, "rows": records})
    path = directory / "nested.parquet"
    records = [
        {"id": 2**53 + 1, "values": [1, 2], "flag": False},
        {"id": 2, "values": None, "flag": True},
    ]
    pq.write_table(pa.Table.from_pylist(records), path)
    cases.append({"name": "parquet", "path": path, "rows": records})
    path = directory / "nested.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as stream:
        stream.write("".join(canonical(row) + "\n" for row in records))
    cases.append({"name": "gzip-jsonl", "path": path, "rows": records})
    for suffix, content in (
        ("txt", "東京 café 🧪\n\nKeep all evidence."),
        ("md", "# Evidence\n\nKeep all evidence."),
    ):
        path = directory / f"evidence.{suffix}"
        path.write_text(content)
        cases.append({"name": suffix, "path": path, "contains": "Keep all evidence."})
    for suffix in ("png", "jpg", "webp"):
        path = directory / f"scan.{suffix}"
        with Image.new("RGB", (1200, 300), "white") as image:
            draw = ImageDraw.Draw(image)
            draw.text(
                (50, 100),
                "Returns are accepted for thirty days.",
                fill="black",
                font=ImageFont.load_default(size=36),
            )
            image.save(path)
        cases.append(
            {
                "name": suffix,
                "path": path,
                "contains": "Returns are accepted for thirty days.",
                "ocr": True,
            }
        )
    for name, content in (
        ("duplicate.csv", b"name,Name\nfirst,second\n"),
        ("wide.csv", b"a,b\n1,2,3\n"),
        ("bad.jsonl", b'{"a":1}\nnot-json\n'),
        ("bad-utf8.txt", b"\xff\xfe\xfa"),
    ):
        path = directory / name
        path.write_bytes(content)
        cases.append({"name": name, "path": path, "reject": True})
    path = directory / "wide-row.jsonl"
    records = [{"id": 1, "text": "東京" * 100000}]
    path.write_text(canonical(records[0]) + "\n")
    cases.append({"name": "wide-row", "path": path, "rows": records, "query_budget": True})
    path = directory / "wide-schema.jsonl"
    records = [{f"column_{index}": index for index in range(201)}]
    path.write_text(canonical(records[0]) + "\n")
    cases.append({"name": "wide-schema", "path": path, "rows": records, "wide_schema": True})
    for size in (10000, 100000):
        path = directory / f"rows-{size}.jsonl"
        with path.open("w") as stream:
            for i in range(size):
                stream.write(
                    canonical(
                        {
                            "id": i,
                            "group": i // 5,
                            "flag": i % 2 == 0,
                            "weight": 0.25,
                            "text": "evidence",
                        }
                    )
                    + "\n"
                )
        cases.append({"name": f"rows-{size}", "path": path, "count": size})
    return cases


async def main(options):
    config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    with (config / "overmind/connection.toml").open("rb") as stream:
        connection = tomllib.load(stream)
    base = connection["base-url"].rstrip("/")
    assert urlsplit(base).hostname in {"localhost", "127.0.0.1"}
    work = options.directory
    work.mkdir(exist_ok=True)
    cases = fixtures(work / "structured")
    for fixture in json.loads((work / "pdfs/manifest.json").read_text()):
        if fixture["kind"] != "batch":
            cases.append(
                {
                    **fixture,
                    "name": fixture["filename"],
                    "path": work / "pdfs" / fixture["filename"],
                    "reject": fixture["expected"] == "error",
                    "pdf": True,
                }
            )
    report = {"run": options.run, "endpoint": base, "cases": [], "calls": [], "success": False}
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"OVERMIND_API_KEY", "OVERMIND_API_URL", "PYTHONPATH"}
    }

    def save():
        options.report.write_text(json.dumps(report, indent=2) + "\n")

    async def cli(*args):
        started = time.monotonic()
        result = await asyncio.to_thread(
            subprocess.run,
            [options.cli, *map(str, args), "--json"],
            cwd=work,
            env=env,
            text=True,
            capture_output=True,
            timeout=600,
        )
        return result.returncode, json.loads(result.stdout), round(time.monotonic() - started, 3)

    async with (
        httpx.AsyncClient(headers={"X-Api-Key": connection["api-key"]}, timeout=90) as http,
        streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()

        async def call(tool_name, **arguments):
            start = time.monotonic()
            result = await session.call_tool(
                tool_name,
                {"project_id": PROJECT, **arguments} if tool_name != "list_projects" else {},
            )
            report["calls"].append(
                {
                    "tool": tool_name,
                    "seconds": round(time.monotonic() - start, 4),
                    "structured_bytes": len(canonical(result.structuredContent).encode()),
                    "wire_bytes": len(result.model_dump_json().encode()),
                    "is_error": result.isError,
                }
            )
            return result

        projects = await call("list_projects")
        assert PROJECT in {p["id"] for p in projects.structuredContent["projects"]}
        report["catalog"] = projects.structuredContent["catalog_sha256"]
        for case in cases:
            if options.only and case["name"] not in options.only:
                continue
            result = {
                "name": case["name"],
                "bytes": case["path"].stat().st_size,
                "sha256": hashlib.sha256(case["path"].read_bytes()).hexdigest(),
                "passed": False,
            }
            report["cases"].append(result)
            tick = time.monotonic()
            try:
                args = [
                    "dataset",
                    "upload",
                    case["path"],
                    "--project-id",
                    PROJECT,
                    "--intent",
                    "explore",
                    "--request-key",
                    f"{options.run}-{case['name']}",
                ]
                status, upload, duration = await cli(*args)
                result["upload_seconds"] = duration
                if status:
                    result["rejection"] = upload
                    assert case.get("reject"), upload
                    result["passed"] = True
                    continue
                dataset = upload["id"]
                result.update(dataset=dataset, transfer=upload["transfer"]["id"])
                progress = []
                while time.monotonic() - tick < 600:
                    answer = await call("get_job", kind="dataset_run", id=dataset)
                    assert not answer.isError, answer.structuredContent
                    job = answer.structuredContent
                    observation = {
                        "status": job["status"],
                        "landing": job["progress"].get("landing"),
                    }
                    if not progress or progress[-1] != observation:
                        progress.append(observation)
                    if job["status"] in {"idle", "error"}:
                        break
                    await asyncio.sleep(1)
                result["observed_progress"] = progress
                result["landing_seconds"] = round(time.monotonic() - tick, 3)
                if case.get("reject"):
                    assert job["status"] == "error", job
                    assert job["job_error"], job
                    result["rejection"] = job["job_error"]
                    result["passed"] = True
                    continue
                assert job["status"] == "idle", {
                    "status": job["status"],
                    "error": job.get("job_error"),
                }
                status, repeated, _ = await cli(*args)
                assert (
                    status == 0
                    and repeated["id"] == dataset
                    and repeated["transfer"]["id"] == result["transfer"]
                )
                answer = await call("inspect_dataset", dataset=dataset, cell_limit=1)
                assert not answer.isError, answer.structuredContent
                detail = answer.structuredContent
                assert detail["cell_page"]["total"] == 1
                cell = detail["active"]
                result.update(cell=cell["id"], rows=cell["rows"], sources=detail["sources"])
                assert detail["sources"][0]["sha256"] == result["sha256"]
                target = Path(tempfile.mkdtemp(dir=work)) / "rows.jsonl"
                status, exported, _ = await cli(
                    "dataset", "export", dataset, "--cell", cell["id"], "--output", target
                )
                assert status == 0, exported
                with target.open() as stream:
                    actual = [json.loads(line) for line in stream]
                assert len(actual) == cell["rows"]
                if "rows" in case:
                    values = [
                        {key: row.get(key) for key in case["rows"][i]}
                        for i, row in enumerate(actual)
                    ]
                    assert canonical(values) == canonical(case["rows"]), {
                        "message": "Input values or types changed during landing",
                        "expected_preview": str(case["rows"][:8])[:500],
                        "actual_preview": str(values[:8])[:500],
                    }
                if case.get("count"):
                    assert len(actual) == case["count"]
                    assert all(
                        row["id"] == i
                        and row["group"] == i // 5
                        and row["flag"] == (i % 2 == 0)
                        and row["weight"] == 0.25
                        for i, row in enumerate(actual)
                    )
                if case.get("contains"):
                    assert case["contains"] in "\n".join(row["text"] for row in actual)
                if case.get("pdf"):
                    metadata = detail["sources"][0]["extraction"]
                    assert metadata["pages"] == case["pages"], metadata
                    assert {row["page"] for row in actual} == set(range(1, case["pages"] + 1))
                    for page in range(1, case["pages"] + 1):
                        text = "\n".join(row["text"] for row in actual if row["page"] == page)
                        if case["kind"] == "native":
                            assert f"Native evidence page {page:04d}" in text, page
                        if case["kind"] == "scanned" or (case["kind"] == "mixed" and page % 3 != 1):
                            assert "Returns are accepted for thirty days." in text, {
                                "page": page,
                                "text": text,
                            }
                    assert all(row["_overmind_provenance"]["evidence"] for row in actual)
                    assert metadata["limitations"]
                queried = await call(
                    "query_dataset",
                    dataset=dataset,
                    sql="SELECT * FROM t ORDER BY source_row",
                    limit=10,
                )
                if case.get("wide_schema"):
                    assert queried.isError
                    assert queried.structuredContent["error"]["code"] == "query_result_too_large"
                    projected = await call(
                        "query_dataset",
                        dataset=dataset,
                        sql="SELECT column_0, column_200 FROM t",
                        limit=1,
                    )
                    assert projected.structuredContent["columns"] == ["column_0", "column_200"]
                    assert projected.structuredContent["rows"] == [
                        {"column_0": 0, "column_200": 200}
                    ]
                elif case.get("query_budget"):
                    result["wide_query_bytes"] = report["calls"][-1]["wire_bytes"]
                    assert result["wide_query_bytes"] <= 128000, (
                        "MCP returned an unbounded wide-row result"
                    )
                    assert queried.isError
                    assert queried.structuredContent["error"]["code"] == "query_result_too_large"
                    aggregate = await call(
                        "query_dataset",
                        dataset=dataset,
                        sql="SELECT length(text) AS characters FROM t",
                        limit=1,
                    )
                    assert aggregate.structuredContent["rows"] == [{"characters": 200000}]
                else:
                    assert not queried.isError, queried.structuredContent
                    assert canonical(queried.structuredContent["rows"]) == canonical(actual[:10])
                result["passed"] = True
            except Exception as error:
                result["error"] = {"type": type(error).__name__, "detail": str(error)[:2000]}
            finally:
                result["seconds"] = round(time.monotonic() - tick, 3)
                save()
                print(
                    json.dumps(
                        {
                            key: result.get(key)
                            for key in ("name", "passed", "rows", "seconds", "error")
                        }
                    ),
                    flush=True,
                )
        report["success"] = all(case["passed"] for case in report["cases"])
        report["latency"] = {}
        for name in sorted({entry["tool"] for entry in report["calls"]}):
            values = sorted(entry["seconds"] for entry in report["calls"] if entry["tool"] == name)
            report["latency"][name] = {
                "count": len(values),
                "median": statistics.median(values),
                "p95": values[min(len(values) - 1, int(len(values) * 0.95))],
                "max": max(values),
            }
        save()
        assert report["success"], "See the per-case report for failed platform expectations."


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument("--cli", required=True)
    parser.add_argument("--only", nargs="*")
    asyncio.run(main(parser.parse_args()))
