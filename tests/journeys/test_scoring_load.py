import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from overbae.tasks.training_experiments import reconcile

from .conftest import RUNS


def test_scoring_load_in_isolated_process(worker):
    env = {
        **os.environ,
        "OVERMIND_LOAD_TEST_DATABASE": f"test_overmind_load_{uuid.uuid4().hex[:8]}",
    }
    # Celery's test-worker teardown sets process-wide stop flags.
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/load/scoring.py",
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
    (RUNS / "scoring-load.log").write_text(result.stdout + result.stderr)
    assert result.returncode == 0, (result.stdout + result.stderr)[-12000:]
    for line in result.stdout.splitlines():
        line = line.lstrip(".")
        if line.startswith(("LATENCY_REPORT=", "READ_LOAD_REPORT=")):
            print(line)
    task = reconcile.delay()
    deadline = time.monotonic() + 10
    while task.id not in worker.finished and time.monotonic() < deadline:
        time.sleep(0.02)
    assert worker.finished.get(task.id) == "SUCCESS", "load test stopped the main journey worker"
