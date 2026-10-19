import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

from overbae.tasks.training_experiments import reconcile

from .conftest import RUNS

ROOT = Path(__file__).resolve().parents[2]


def _run(args, env, log_name, timeout):
    RUNS.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        args,
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    lines = []

    def read():
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)
            print(line, end="", flush=True)

    reader = threading.Thread(target=read)
    reader.start()
    try:
        code = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        reader.join()
        raise
    reader.join()
    text = "".join(lines)
    (RUNS / log_name).write_text(text)
    assert code == 0, failure_output(text)
    return text


def test_read_load_in_isolated_process(worker):
    env = {
        **os.environ,
        "OVERMIND_LOAD_TEST_DATABASE": f"test_overmind_load_{uuid.uuid4().hex[:8]}",
        "TEST_REDIS_URL": os.environ["TEST_REDIS_URL"].rsplit("/", 1)[0] + "/12",
    }
    # Celery's test-worker teardown sets process-wide stop flags.
    text = _run(
        [sys.executable, "-m", "pytest", "tests/load/reads.py", "--ds=tests.load.settings", "-v"],
        env,
        "read-load.log",
        240,
    )
    for line in text.splitlines():
        line = line.lstrip(".")
        if line.startswith(("LATENCY_REPORT=", "READ_LOAD_REPORT=")):
            print(line)
    task = reconcile.delay()
    deadline = time.monotonic() + 10
    while task.id not in worker.finished and time.monotonic() < deadline:
        time.sleep(0.02)
    assert worker.finished.get(task.id) == "SUCCESS", "load test stopped the main journey worker"


def test_mixed_capacity_and_recovery_in_isolated_process(worker):
    env = {
        **os.environ,
        "OVERMIND_LOAD_TEST_DATABASE": f"test_overmind_capacity_{uuid.uuid4().hex[:8]}",
        "TEST_REDIS_URL": os.environ["TEST_REDIS_URL"].rsplit("/", 1)[0] + "/12",
        "OVERMIND_SOAK_SECONDS": "30",
        "OVERMIND_SOAK_CLIENTS": "8",
        "OVERMIND_SOAK_ROWS": "8192",
        "OVERMIND_SOAK_REPORT": str(RUNS / "mixed-capacity.json"),
    }
    _run(
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
            "-v",
        ],
        env,
        "mixed-capacity.log",
        420,
    )
    task = reconcile.delay()
    deadline = time.monotonic() + 10
    while task.id not in worker.finished and time.monotonic() < deadline:
        time.sleep(0.02)
    assert worker.finished.get(task.id) == "SUCCESS", "capacity test stopped the journey worker"


def failure_output(text):
    lines = text.splitlines()
    return "\n".join(line for line in lines if not line.startswith('{"levelname":'))[-20000:]
