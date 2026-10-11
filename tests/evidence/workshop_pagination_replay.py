import argparse
import hashlib
import json
import time
from pathlib import Path

import httpx
from overmind.transfer_connection import resolve_transfer_connection

DATASET = "ecabb7ed-4b75-4f2b-8572-35223f1c414c"
CELL = "e5563a89-f9b2-4dfd-bd68-c1dc6c71babd"
PROJECT = "a279d834-899c-4ac6-888a-157f0c7d9cf8"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--compare", type=Path)
    args = parser.parse_args()
    key, base = resolve_transfer_connection("", "", None)
    assert base == "http://localhost:8000"
    results = []
    with httpx.Client(base_url=base, headers={"X-Api-Key": key}, timeout=120) as client:
        for offset, limit in [(10, 10), (20, 10), (0, 50), (10000, 25), (1000000, 10)]:
            params = {"cell": CELL, "offset": offset, "limit": limit, "diff": "1"}
            start = time.monotonic()
            response = client.get(f"/api/datasets/{DATASET}/rows/", params=params)
            result = {
                "offset": offset,
                "limit": limit,
                "seconds": round(time.monotonic() - start, 4),
                "status": response.status_code,
                "bytes": len(response.content),
            }
            if response.is_success:
                body = response.json()
                assert body["total"] == 1034657
                assert [r["_index"] for r in body["rows"]] == list(range(offset, offset + limit))
                result["sha256"] = hashlib.sha256(
                    json.dumps(body, sort_keys=True, ensure_ascii=False).encode()
                ).hexdigest()
                result["rows"] = len(body["rows"])
            results.append(result)
            print(json.dumps(result), flush=True)
    if args.compare:
        before = json.loads(args.compare.read_text())["requests"]
        for old, new in zip(before, results, strict=True):
            assert new["status"] == 200, new
            if old["status"] == 200:
                assert old["sha256"] == new["sha256"], (old, new)
    args.output.write_text(
        json.dumps(
            {
                "endpoint": base,
                "project": PROJECT,
                "dataset": DATASET,
                "cell": CELL,
                "requests": results,
            },
            indent=2,
        )
        + "\n"
    )


if __name__ == "__main__":
    main()
