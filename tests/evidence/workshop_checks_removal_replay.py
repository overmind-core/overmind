import asyncio
import json
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from overmind.transfer_connection import resolve_transfer_connection

PROJECT = "e18b29b5-915d-45a7-80cd-77ffe6559205"
DATASET = "7b876858-e8a2-4d60-8361-ecee802e9b90"
SOURCE = "36523ae9-6675-49aa-9ac8-7d497d47aa6a"
SOURCE_FINGERPRINT = "f6498b234e8621cc1b710b0ad2db506520c57be7efd6235199f738eea7623c1f"
FINAL = "63806e31-001c-4272-91c5-014db4b7fefb"
FINAL_FINGERPRINT = "a0dc75649dd72e9080bdabf2d7c446a75a0c44fa62eabce90bb7a9e0c03ad41a"


async def main():
    key, base = resolve_transfer_connection("", "", None)
    assert base == "http://localhost:8000"
    evidence = {"endpoint": base, "project": PROJECT, "dataset": DATASET, "runs": []}
    old = json.loads(Path("tests/evidence/kyc_preparation_audit/receipts.json").read_text())
    async with (
        httpx.AsyncClient(headers={"X-Api-Key": key}, timeout=120) as http,
        streamable_http_client(base + "/api/mcp/", http_client=http) as (read, write, _),
        ClientSession(read, write) as session,
    ):
        await session.initialize()
        projects = (await session.call_tool("list_projects", {"limit": 100})).structuredContent
        assert projects["connection"]["mcp_url"] == base + "/api/mcp/"
        assert PROJECT in {p["id"] for p in projects["projects"]}
        tools = (await session.list_tools()).tools
        names = {tool.name for tool in tools}
        retired = {"inspect_dataset_preparation", "record_dataset_findings"}
        assert not names & retired
        assert not any(name in tool.description for tool in tools for name in retired)
        for name in retired:
            result = await session.call_tool(name, {"project_id": PROJECT, "dataset": DATASET})
            assert result.isError and result.structuredContent["error"]["code"] == "invalid_tool"
        identity = json.loads(
            (await session.read_resource("overmind://interface/current")).contents[0].text
        )
        assert identity["contract_version"] == "7.0.0"
        evidence["interface"] = identity

        async def call(name, **arguments):
            result = await session.call_tool(name, {"project_id": PROJECT, **arguments})
            assert not result.isError, result.structuredContent
            return result.structuredContent

        for method, path in [("GET", "preparation-checks"), ("POST", "preparation-findings")]:
            result = await http.request(method, base + f"/api/datasets/{DATASET}/{path}/")
            assert result.status_code == 404
        response = await http.get(base + f"/api/datasets/{DATASET}/")
        response.raise_for_status()
        rest = response.json()
        assert "preparation_status" not in rest
        assert all("preparation_status" not in cell for cell in rest["cells"])
        mcp = await call("inspect_dataset", dataset=DATASET)
        assert "preparation_status" not in mcp
        assert all("preparation_status" not in cell for cell in mcp["cells"])
        assert mcp["active"]["id"] == FINAL
        assert mcp["active"]["fingerprint"] == FINAL_FINGERPRINT
        assert mcp["active"]["rows"] == 10000
        workbench = await call("inspect_dataset_workbench", dataset=DATASET, limit=1)
        assert "preparation_status" not in workbench
        assert len(workbench["preparation"]["nodes"]) == 4
        assert workbench["current_pipeline"] == old["execution"]["corrected_recipe"]["id"]
        response = await http.get(base + f"/api/datasets/{DATASET}/preparation/")
        response.raise_for_status()
        assert {n["cell"]["id"] for n in response.json()["nodes"]} == {
            n["cell"] for n in workbench["preparation"]["nodes"]
        }
        evidence["existing_data_preserved"] = mcp["active"]
        evidence["process"] = workbench["preparation"]
        cases = [
            ("valid", workbench["current_pipeline"], {}, "completed", None),
            (
                "drop",
                old["execution"]["controls_recipe"]["id"],
                {"fault": "drop"},
                "failed",
                "preserve_rows",
            ),
            (
                "invalid-target",
                old["execution"]["controls_recipe"]["id"],
                {"fault": "bad_probabilities"},
                "failed",
                "normalized",
            ),
        ]
        for case, recipe, parameters, expected, error in cases:
            submitted = await call(
                "run_dataset_pipeline",
                dataset=DATASET,
                pipeline=recipe,
                source_cell=SOURCE,
                source_fingerprint=SOURCE_FINGERPRINT,
                request_key=f"checks-removal-20261010-{case}",
                mode="preview",
                preview_rows=100,
                parameters=parameters,
            )
            deadline = time.monotonic() + 120
            while time.monotonic() < deadline:
                job = await call("get_job", kind="dataset_pipeline", id=submitted["job"]["id"])
                if job["status"] in {"completed", "failed", "cancelled"}:
                    break
                await asyncio.sleep(max(1, job["details"].get("poll_after_seconds") or 2))
            assert job["status"] == expected, job
            if error:
                assert error in job["details"]["error"]
            assert job["details"]["output_cell"] is None
            evidence["runs"].append(job)
            print(json.dumps({"case": case, "status": job["status"], "job": job["id"]}), flush=True)
        after = await call("inspect_dataset", dataset=DATASET)
        assert after["active"] == mcp["active"]
        evidence["assertions"] = [
            "Removed tools absent and rejected",
            "Removed REST endpoints return 404",
            "Dataset/cell/workbench payloads omit preparation status",
            "Existing 10,000-row output and four-cell process preserved",
            "Automatic retained-script association preserved",
            "Valid isolated preview completes",
            "Row-loss and invalid-target publication safeguards remain active",
            "Previews preserve the selected published output",
        ]
        Path("tests/evidence/workshop-checks-removal-live.json").write_text(
            json.dumps(evidence, indent=2) + "\n"
        )
        print(json.dumps({"checks": evidence["assertions"]}))


if __name__ == "__main__":
    asyncio.run(main())
