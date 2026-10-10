import argparse
import json
import os
import tomllib
from pathlib import Path

import requests

from modal_shared.training_monitoring import fingerprint

PROJECT = "1e3f3e92-b50d-4590-85ed-97921d132d3c"
JOBS = [
    "b6500866-ed71-4a16-b0ce-20bdc4b4b77f",
    "9157305b-6b8b-4778-8dbc-cf66c162a785",
    "5907d62a-d4a3-4063-a704-60f21c5bbbc4",
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", action="append", default=[])
    parser.add_argument(
        "--output", type=Path, default=Path("tests/evidence/unsloth-decision-surface-parity.json")
    )
    args = parser.parse_args()
    config = (
        Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        / "overmind/connection.toml"
    )
    saved = tomllib.loads(config.read_text())
    base = saved["base-url"].rstrip("/")
    assert base == "http://localhost:8000"
    session = requests.Session()
    session.headers["X-Api-Key"] = saved["api-key"]

    def get(path, params):
        response = session.get(base + path, params=params, timeout=60, allow_redirects=False)
        response.raise_for_status()
        return response.json()

    def rpc(method, params):
        response = session.post(
            base + "/api/mcp/",
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2025-06-18",
            },
            timeout=60,
            allow_redirects=False,
        )
        response.raise_for_status()
        result = response.json()
        assert "error" not in result, result
        return result["result"]

    rpc(
        "initialize",
        {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "decision-surface-parity", "version": "1"},
        },
    )
    receipts = []
    for job in [*JOBS, *args.job]:
        result = rpc(
            "tools/call",
            {
                "name": "inspect_training_progress",
                "arguments": {"project_id": PROJECT, "job": job, "limit": 100},
            },
        )
        assert not result.get("isError"), result
        mcp = result["structuredContent"]["progress"]
        rest = get(f"/api/finetuning-jobs/{job}/monitoring/", {"limit": 100})
        indexed = {check["id"]: check for check in rest["checks"]}
        compared = []
        for check in mcp["checks"]:
            if check["state"] != "completed":
                continue
            other = indexed[check["id"]]
            for collection in check.get("collections", []):
                items, offset = [], 0
                while offset is not None:
                    params = {"field": collection["field"], "limit": 100, "offset": offset}
                    page = rpc(
                        "tools/call",
                        {
                            "name": "inspect_training_progress",
                            "arguments": {"project_id": PROJECT, "job": job, **params},
                        },
                    )
                    assert not page.get("isError"), page
                    detail = page["structuredContent"]["progress"]
                    assert detail == get(f"/api/finetuning-jobs/{job}/monitoring-evidence/", params)
                    assert detail["sha256"] == collection["sha256"]
                    items.extend(detail["items"])
                    offset = detail["next_offset"]
                assert len(items) == collection["count"]
                value = (
                    {item["key"]: item["value"] for item in items}
                    if collection["type"] == "object"
                    else items
                )
                assert fingerprint(value) == collection["sha256"]
                *parents, leaf = collection["field"].split("/")[3:]
                target = check
                for parent in parents:
                    target = target.setdefault(parent, {})
                target[leaf] = value
            for key in ("metrics", "coverage", "facts", "evidence_sha256", "sample_fingerprint"):
                assert check[key] == other[key], (job, check["id"], key)
            compared.append(check["id"])
        assert compared
        evidence_args = {"project_id": PROJECT, "job": job, "check": compared[0], "limit": 2}
        examples = rpc(
            "tools/call", {"name": "inspect_training_progress", "arguments": evidence_args}
        )["structuredContent"]["progress"]
        rest_examples = get(
            f"/api/finetuning-jobs/{job}/monitoring-evidence/", {"check": compared[0], "limit": 2}
        )
        assert examples == rest_examples
        if job == "5907d62a-d4a3-4063-a704-60f21c5bbbc4":
            assert mcp["status"] == "cancelled"
            assert any(c["state"] == "available" for c in mcp["checkpoints"])
            assert all(c["facts"]["measurement"] == "native_probabilities" for c in mcp["checks"])
            assert all(item["options"] and item["question"] for item in examples["items"])
        receipts.append(
            {
                "job": job,
                "compared_checks": compared,
                "examples": len(examples["items"]),
                "evidence_sha256": examples["sha256"],
                "status": "passed",
            }
        )
    args.output.write_text(json.dumps(receipts, indent=2) + "\n")
    print(json.dumps(receipts))


if __name__ == "__main__":
    main()
