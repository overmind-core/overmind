#!/usr/bin/env python3
"""Decision-model fine-tune and base inference through the Docker API onto Modal.

Does not call Modal itself. The compose API spawns overmind-decision in
MODAL_ENVIRONMENT (overmind-dev).

  auth     POST /api/auth/token/
  catalog  GET  /api/decision-models/
  infer    POST /api/decision-models/{id}/systemone   (the published checkpoint)
  train    POST /api/decision-jobs/                   (one epoch, two short records)
  poll     GET  /api/decision-jobs/{id}/

Usage:
  python scripts/e2e_decision_docker.py
  python scripts/e2e_decision_docker.py --models vllm-sr/d3-edge
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = Path(__file__).with_name("e2e_decision_docker_results.json")
API_BASE = os.environ.get("API_BASE", "http://localhost:8000/api").rstrip("/")
EMAIL = os.environ.get("E2E_EMAIL", "rohit@overmindlab.ai")
PASSWORD = os.environ.get("E2E_PASSWORD", "admin123")
PROJECT_ID = os.environ.get("E2E_PROJECT_ID", "5efc1a60-e5d6-4c6c-bd9c-a9334b7cb936")

RECORDS = [
    {
        "state": "The invoice is unpaid.",
        "questions": {
            "pay": {
                "type": "choice",
                "instructions": "Should the invoice be paid?",
                "criteria": {"yes": "Pay it", "no": "Do not pay it"},
            }
        },
        "gold": {"pay": "yes"},
    },
    {
        "state": "The shipment arrived damaged.",
        "questions": {
            "pay": {
                "type": "choice",
                "instructions": "Should the invoice be paid?",
                "criteria": {"yes": "Pay it", "no": "Do not pay it"},
            }
        },
        "gold": {"pay": "no"},
    },
]

INFER_BODY = {
    "state": "The invoice is unpaid.",
    "questions": {
        "pay": {
            "type": "choice",
            "instructions": "Should the invoice be paid?",
            "criteria": {"yes": "Pay it", "no": "Do not pay it"},
        },
        "late": {"type": "noul", "instructions": "Is the invoice late?"},
        "risk": {
            "type": "score",
            "instructions": "How risky is paying it?",
            "criteria": ["low", "high"],
        },
    },
}


def _fail(message: str) -> None:
    raise SystemExit(message)


def migrate() -> None:
    subprocess.run(
        ["docker", "compose", "exec", "-T", "api", "python", "manage.py", "migrate", "--noinput"],
        cwd=ROOT,
        check=True,
    )


def token() -> str:
    response = requests.post(
        f"{API_BASE}/auth/token/",
        json={"email": EMAIL, "password": PASSWORD},
        timeout=30,
    )
    if not response.ok:
        _fail(f"auth failed: {response.status_code} {response.text[:300]}")
    return response.json()["access"]


def headers(access: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}


def catalog(access: str) -> list[str]:
    response = requests.get(f"{API_BASE}/decision-models/", headers=headers(access), timeout=30)
    if not response.ok:
        _fail(f"catalog failed: {response.status_code} {response.text[:300]}")
    return [row["id"] for row in response.json()]


def infer(access: str, model_id: str) -> dict:
    response = requests.post(
        f"{API_BASE}/decision-models/{model_id}/systemone",
        headers=headers(access),
        json={"project": PROJECT_ID, **INFER_BODY},
        timeout=2 * 60 * 60,
    )
    body = response.json() if response.content else {}
    answers = body.get("answers") if isinstance(body, dict) else None
    errors = {
        key: value.get("error")
        for key, value in (answers or {}).items()
        if isinstance(value, dict) and value.get("error")
    }
    return {
        "ok": response.status_code == 200 and bool(answers) and not errors,
        "status": response.status_code,
        "model": body.get("model") if isinstance(body, dict) else None,
        "usage": body.get("usage") if isinstance(body, dict) else None,
        "errors": errors,
        "detail": body.get("detail") if isinstance(body, dict) else response.text[:300],
    }


def train(access: str, model_id: str) -> dict:
    started = requests.post(
        f"{API_BASE}/decision-jobs/",
        headers=headers(access),
        json={"project": PROJECT_ID, "model": model_id, "records": RECORDS},
        timeout=60,
    )
    if started.status_code != 202:
        return {"ok": False, "status": started.status_code, "detail": started.text[:500]}
    job = started.json()
    job_id = job["id"]
    deadline = time.time() + 6 * 60 * 60
    while time.time() < deadline:
        current = requests.get(
            f"{API_BASE}/decision-jobs/{job_id}/",
            headers=headers(access),
            timeout=60,
        )
        if not current.ok:
            return {
                "ok": False,
                "job_id": job_id,
                "status": current.status_code,
                "detail": current.text[:500],
            }
        row = current.json()
        if row["status"] in {"succeeded", "failed"}:
            return {
                "ok": row["status"] == "succeeded",
                "job_id": job_id,
                "status": row["status"],
                "error": row.get("error"),
                "result": row.get("result"),
            }
        time.sleep(30)
    return {"ok": False, "job_id": job_id, "status": "timeout"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=None)
    parser.add_argument("--skip-migrate", action="store_true")
    parser.add_argument("--only", choices=("infer", "train", "both"), default="both")
    args = parser.parse_args()
    if not args.skip_migrate:
        migrate()
    access = token()
    model_ids = args.models or catalog(access)
    previous = {}
    if RESULTS_PATH.exists():
        try:
            previous = {row["model"]: row for row in json.loads(RESULTS_PATH.read_text())}
        except (json.JSONDecodeError, OSError):
            previous = {}
    rows = []
    for model_id in model_ids:
        row = previous.get(model_id, {"model": model_id})
        row["model"] = model_id
        print(f"\n=== {model_id} ===", flush=True)
        if args.only in {"infer", "both"}:
            print("infer", flush=True)
            row["infer"] = infer(access, model_id)
            print(json.dumps(row["infer"]), flush=True)
        if args.only in {"train", "both"}:
            print("train", flush=True)
            row["train"] = train(access, model_id)
            print(json.dumps({k: v for k, v in row["train"].items() if k != "result"}), flush=True)
        rows.append(row)
        previous[model_id] = row
        RESULTS_PATH.write_text(json.dumps(list(previous.values()), indent=2) + "\n")
    failed = [
        row["model"]
        for row in rows
        if (args.only != "train" and not row.get("infer", {}).get("ok"))
        or (args.only != "infer" and not row.get("train", {}).get("ok"))
    ]
    print(json.dumps({"models": len(rows), "failed": failed}), flush=True)
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
