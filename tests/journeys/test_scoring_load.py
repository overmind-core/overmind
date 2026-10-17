import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from overbae.tasks.training_experiments import reconcile

from .conftest import RUNS


def test_read_load_in_isolated_process(worker):
    env = {
        **os.environ,
        "OVERMIND_LOAD_TEST_DATABASE": f"test_overmind_load_{uuid.uuid4().hex[:8]}",
        "TEST_REDIS_URL": os.environ["TEST_REDIS_URL"].rsplit("/", 1)[0] + "/12",
    }
    # Celery's test-worker teardown sets process-wide stop flags.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/load/reads.py",
            "--ds=tests.load.settings",
            "-q",
            "-s",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=240,
    )
    RUNS.mkdir(parents=True, exist_ok=True)
    (RUNS / "read-load.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, failure_output(result)
    for line in result.stdout.splitlines():
        line = line.lstrip(".")
        if line.startswith(("LATENCY_REPORT=", "READ_LOAD_REPORT=")):
            print(line)
    task = reconcile.delay()
    deadline = time.monotonic() + 10
    while task.id not in worker.finished and time.monotonic() < deadline:
        time.sleep(0.02)
    assert worker.finished.get(task.id) == "SUCCESS", "load test stopped the main journey worker"


def test_mixed_capacity_and_recovery_in_isolated_process(worker):
    RUNS.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "OVERMIND_LOAD_TEST_DATABASE": f"test_overmind_capacity_{uuid.uuid4().hex[:8]}",
        "TEST_REDIS_URL": os.environ["TEST_REDIS_URL"].rsplit("/", 1)[0] + "/12",
        "OVERMIND_SOAK_SECONDS": "30",
        "OVERMIND_SOAK_CLIENTS": "8",
        "OVERMIND_SOAK_ROWS": "8192",
        "OVERMIND_SOAK_REPORT": str(RUNS / "mixed-capacity.json"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/load/mixed.py",
            "tests/load/isolation.py",
            "tests/load/recovery.py",
            "tests/load/transfers.py",
            "tests/load/landing.py",
            "tests/load/landing_burst.py",
            "tests/load/query_capacity.py",
            "--ds=tests.load.settings",
            "-q",
            "-s",
        ],
        cwd=Path(__file__).resolve().parents[2],
        env=env,
        capture_output=True,
        text=True,
        timeout=420,
    )
    (RUNS / "mixed-capacity.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, failure_output(result)
    task = reconcile.delay()
    deadline = time.monotonic() + 10
    while task.id not in worker.finished and time.monotonic() < deadline:
        time.sleep(0.02)
    assert worker.finished.get(task.id) == "SUCCESS", "capacity test stopped the journey worker"


def failure_output(result):
    lines = (result.stdout + result.stderr).splitlines()
    return "\n".join(line for line in lines if not line.startswith('{"levelname":'))[-20000:]
