import os
import threading

import pandas as pd
import pytest
from test_sandbox import run

from sandbox import tables

# These promises hold only for a sandbox that runs as root and starts each run as its
# own uid on a network without a route out: the compose `sandbox` service.
URL = os.environ.get("SANDBOX_ISOLATION_URL", "")
TOKEN = os.environ.get("SANDBOX_ISOLATION_TOKEN", "")
pytestmark = pytest.mark.skipif(
    not URL, reason="SANDBOX_ISOLATION_URL names a root sandbox on an internal network"
)


@pytest.fixture
def frame(tmp_path):
    path = tmp_path / "in.parquet"
    tables.write_frame(path, pd.DataFrame({"text": ["a", "b"]}))
    return path


def test_a_script_cannot_read_the_server_environment(frame):
    script = """
import os, pathlib
for pid in (os.getppid(), 1):
    try:
        data = pathlib.Path(f"/proc/{pid}/environ").read_bytes()
        print("read", pid, data)
    except PermissionError:
        print("denied", pid)
"""

    _, header, _ = run(URL, TOKEN, script, frame, mode="inspect")

    assert header["ok"] is True, header["error"]
    assert header["stdout"].count("denied") == 2
    assert "SANDBOX_TOKEN" not in header["stdout"]
    assert "read" not in header["stdout"]


def test_a_script_has_no_route_out(frame):
    script = """
import multiprocessing.connection
for address in (("1.1.1.1", 443), ("pypi.org", 443)):
    try:
        multiprocessing.connection.Client(address)
        print("connected", address[0])
    except OSError as exc:
        print("refused", address[0], type(exc).__name__)
"""

    _, header, _ = run(URL, TOKEN, script, frame, mode="inspect")

    assert header["ok"] is True, header["error"]
    assert "connected" not in header["stdout"]
    assert header["stdout"].count("refused") == 2


def test_a_script_cannot_read_another_run(frame):
    holder = threading.Thread(
        target=run,
        args=(URL, TOKEN, "import time\ntime.sleep(8)", frame),
        kwargs={"mode": "inspect"},
    )
    holder.start()
    script = """
import os, pathlib, time
time.sleep(2)
mine = pathlib.Path.cwd().name
for name in sorted(os.listdir("/tmp")):
    if not name.startswith("run_") or name == mine:
        continue
    try:
        print("listed", name, os.listdir(f"/tmp/{name}"))
    except PermissionError:
        print("denied", name)
"""
    try:
        _, header, _ = run(URL, TOKEN, script, frame, mode="inspect")
    finally:
        holder.join()

    assert header["ok"] is True, header["error"]
    assert "denied run_" in header["stdout"]
    assert "listed" not in header["stdout"]
