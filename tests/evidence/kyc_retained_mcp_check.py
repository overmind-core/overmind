import argparse
import itertools
import json
import subprocess
from pathlib import Path

import anyio
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


async def check(job, source):
    configured = subprocess.run(
        ["/usr/local/bin/codex", "mcp", "get", "overmind", "--json"],
        capture_output=True,
        text=True,
        check=True,
    )
    transport = json.loads(configured.stdout)["transport"]
    assert transport["url"] == "http://localhost:8000/api/mcp/"
    async with (
        httpx.AsyncClient(headers=transport.get("http_headers", {}), timeout=60) as client,
        streamable_http_client(transport["url"], http_client=client) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        resource = await session.read_resource(
            f"overmind://jobs/dataset_pipeline/{job}?project_id=e18b29b5-915d-45a7-80cd-77ffe6559205"
        )
        payload = json.loads(resource.contents[0].text)
    assert payload["state"] == "completed", payload.get("error")
    assert payload["execution"] == "isolated_container"
    with source.open() as handle:
        prefix = {row["source_row"]: row for row in map(json.loads, itertools.islice(handle, 100))}
    steps = payload["result"]["steps"]
    samples_verified = 0
    for step in steps:
        assert step["exit_code"] == 0
        assert step["check_results"]["passed"]["preserve_rows"]
        assert not step["check_results"]["failed"]
        for row in step.get("sample", []):
            original = prefix[row["source_row"]]
            if step["step_id"] == "validate":
                assert row["tokens"] == original["tokens"]
                assert row["kyc_risk_bucket"] == original["kyc_risk_bucket"]
            else:
                assert row["question"] == original["tokens"]
                assert row["answer"] == original["kyc_risk_bucket"]
            if step["step_id"] == "messages":
                assert row["messages"] == [
                    {"role": "user", "content": original["tokens"]},
                    {"role": "assistant", "content": original["kyc_risk_bucket"]},
                ]
            samples_verified += 1
    if payload["mode"] == "preview":
        assert samples_verified > 0, "Preview evidence was not inspected"
    print(
        json.dumps(
            {
                "job": job,
                "execution": payload["execution"],
                "state": payload["state"],
                "mode": payload["mode"],
                "samples_verified": samples_verified,
                "output_cell": payload["output_cell"],
                "seconds": payload["result"]["seconds"],
                "steps": [
                    {
                        "step": step["step_id"],
                        "input_rows": step["input_rows"],
                        "output_rows": step["output_rows"],
                        "checks": step["check_results"],
                        "seconds": step["seconds"],
                    }
                    for step in steps
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("job")
    parser.add_argument("source", type=Path)
    arguments = parser.parse_args()
    anyio.run(check, arguments.job, arguments.source)
