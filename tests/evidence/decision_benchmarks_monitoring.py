import json
from pathlib import Path

import anyio
import httpx
from decision_benchmarks_live import PROJECT, ROOT, command
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from modal_shared.training_monitoring import fingerprint
from modal_shared.training_release import identity


async def main():
    transport = command("/usr/local/bin/codex", "mcp", "get", "overmind", "--json")["transport"]
    assert transport["url"] == "http://localhost:8000/api/mcp/"
    evidence = json.loads((ROOT / "decision-benchmarks-results.json").read_text())
    verified = []
    async with (
        httpx.AsyncClient(headers=transport.get("http_headers", {}), timeout=120) as client,
        streamable_http_client(transport["url"], http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        projects = (await session.call_tool("list_projects", {})).structuredContent
        assert projects["connection"]["mcp_url"] == transport["url"]
        assert PROJECT in {p["id"] for p in projects["projects"]}

        async def inspect(job, **kwargs):
            result = await session.call_tool(
                "inspect_training_progress", {"project_id": PROJECT, "job": job, **kwargs}
            )
            assert not result.isError, result.structuredContent
            return result.structuredContent["progress"]

        for benchmark, case in evidence["cases"].items():
            for job in case["observation"]["progress"]["jobs"]:
                detail = await inspect(job["job_id"])
                retained = []
                for entry in detail["checks"] + detail["checkpoints"]:
                    for collection in entry.get("collections", []):
                        offset = 0
                        value = {} if collection["type"] == "object" else []
                        while offset is not None:
                            page = await inspect(
                                job["job_id"], field=collection["field"], offset=offset, limit=10
                            )
                            assert page["sha256"] == collection["sha256"]
                            if isinstance(value, dict):
                                value.update({item["key"]: item["value"] for item in page["items"]})
                            else:
                                value.extend(page["items"])
                            offset = page["next_offset"]
                        assert len(value) == collection["count"]
                        assert fingerprint(value) == collection["sha256"]
                        retained.append(collection)
                result = {
                    "benchmark": benchmark,
                    "job": job["job_id"],
                    "status": detail["status"],
                    "overview_bytes": len(json.dumps(detail).encode()),
                    "collections": retained,
                    "checks": [
                        {
                            key: check[key]
                            for key in (
                                "id",
                                "step",
                                "stream",
                                "state",
                                "metrics",
                                "coverage",
                                "error",
                            )
                        }
                        for check in detail["checks"]
                    ],
                    "checkpoints": [
                        {
                            key: checkpoint[key]
                            for key in ("id", "step", "state", "verification", "selected")
                        }
                        for checkpoint in detail["checkpoints"]
                    ],
                }
                verified.append(result)
                assert not any(
                    check["error"] or check["state"] == "failed" for check in result["checks"]
                ), result
                print(
                    json.dumps(
                        {
                            k: v
                            for k, v in result.items()
                            if k not in {"collections", "checks", "checkpoints"}
                        }
                    ),
                    flush=True,
                )
    runtime = identity(Path(__file__).resolve().parents[2])
    assert runtime["release"] == "07e8a773f7d959be0cef1d5dff328df1fea81a8377fe14c2c1e5163038868053"
    (ROOT / "decision-benchmarks-monitoring.json").write_text(
        json.dumps(
            {"endpoint": transport["url"], "runtime": runtime, "verified": verified}, indent=2
        )
        + "\n"
    )


if __name__ == "__main__":
    anyio.run(main)
