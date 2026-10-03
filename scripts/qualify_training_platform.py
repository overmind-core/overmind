import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path


def working_changes(root):
    tracked = (
        subprocess.check_output(["git", "diff", "--name-only", "HEAD", "-z"], cwd=root)
        .decode()
        .split("\0")
    )
    added = (
        subprocess.check_output(
            ["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=root
        )
        .decode()
        .split("\0")
    )
    return {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        if (root / name).is_file()
        else None
        for name in sorted(set(tracked + added) - {""})
    }


def main():
    parser = argparse.ArgumentParser(
        description="Qualify training and workshop changes without live services or GPU calls."
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "DJANGO_SETTINGS_MODULE": "tests.settings", "UV_NO_SYNC": "1"}
    checks = [
        (
            "backend",
            root,
            [sys.executable, "-m", "pytest", "tests/", "-q", "-ra", "--no-cov", "--tb=short"],
        ),
        ("backend_lint", root, [str(root / ".venv/bin/ruff"), "check", "."]),
        ("backend_format", root, [str(root / ".venv/bin/ruff"), "format", "--check", "."]),
        ("frontend", root / "frontend", ["bun", "run", "test"]),
        ("frontend_lint", root / "frontend", ["bun", "run", "check"]),
        ("build", root / "frontend", ["bun", "run", "build"]),
        (
            "sdk",
            root / "overmind",
            [
                "uv",
                "run",
                "--no-sync",
                "python",
                "-m",
                "pytest",
                "tests/",
                "--ignore=tests/test_spans.py",
                "-q",
                "-ra",
            ],
        ),
        ("sdk_lint", root / "overmind", ["uv", "run", "--no-sync", "ruff", "check"]),
        ("sdk_format", root / "overmind", ["uv", "run", "--no-sync", "ruff", "format", "--check"]),
        ("types", root / "frontend", ["bun", "run", "typecheck"]),
        ("design", root / "frontend", ["bun", "run", "check:all"]),
        ("schema", root, [sys.executable, "manage.py", "makemigrations", "--check", "--dry-run"]),
        ("migration_execution", root, [sys.executable, "manage.py", "migrate", "--noinput"]),
    ]
    receipt = {
        "started_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip(),
        "working_changes_at_start": working_changes(root),
        "environment": {
            "database": "in-memory SQLite via tests.settings",
            "python": sys.version,
            "root": str(root),
        },
        "limits": [
            "Provider calls are replaced by workflow fixtures; no live worker deployment or GPU qualification.",
            "SQLite migration execution does not prove production PostgreSQL lock/concurrency behavior.",
            "Browser visual qualification and network transfer have separate receipts.",
        ],
        "checks": [],
    }
    for name, cwd, command in checks:
        print(f"Starting {name}", flush=True)
        start = time.monotonic()
        with (output / f"{name}.log").open("w") as log:
            result = subprocess.run(
                command, cwd=cwd, env=env, stdout=log, stderr=subprocess.STDOUT, check=False
            )
        receipt["checks"].append(
            {
                "name": name,
                "command": command,
                "cwd": str(cwd),
                "exit_code": result.returncode,
                "seconds": time.monotonic() - start,
                "log": f"{name}.log",
            }
        )
        receipt["passed"] = all(check["exit_code"] == 0 for check in receipt["checks"])
        (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(f"{name}: exit {result.returncode}", flush=True)
    receipt["working_changes_at_finish"] = working_changes(root)
    receipt["source_changed_during_run"] = (
        receipt["working_changes_at_start"] != receipt["working_changes_at_finish"]
    )
    (output / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return 0 if receipt["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
