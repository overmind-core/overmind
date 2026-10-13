import io
import os
import threading
from pathlib import Path

import httpx
import pandas as pd
import pyarrow.parquet as pq
import pytest
from test_sandbox import run

from sandbox import tables
from sandbox.protocol import HeaderReader, header_bytes

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


def test_a_script_holds_no_capabilities(frame):
    script = """
import pathlib
status = pathlib.Path("/proc/self/status").read_text()
print([line.split()[1] for line in status.splitlines() if line.startswith(("CapEff", "CapPrm", "CapAmb"))])
"""

    _, header, _ = run(URL, TOKEN, script, frame, mode="inspect")

    assert header["ok"] is True, header["error"]
    assert header["stdout"].strip() == str(["0000000000000000"] * 3)


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


def test_a_script_cannot_find_or_read_another_run(frame):
    holder = threading.Thread(
        target=run,
        args=(URL, TOKEN, "import time\ntime.sleep(8)", frame),
        kwargs={"mode": "inspect"},
    )
    holder.start()
    script = """
import os, pathlib, time
time.sleep(2)
try:
    print("listed", os.listdir("/tmp/sandbox"))
except PermissionError:
    print("denied listing")
mine = pathlib.Path.cwd()
try:
    mine.rename(mine.with_name("moved"))
    print("renamed")
except PermissionError:
    print("denied rename")
"""
    try:
        _, header, _ = run(URL, TOKEN, script, frame, mode="inspect")
    finally:
        holder.join()

    assert header["ok"] is True, header["error"]
    assert "denied listing" in header["stdout"]
    assert "denied rename" in header["stdout"]


@pytest.mark.parametrize(
    "make",
    [
        "os.symlink('/etc/passwd', 'output.parquet')",
        "os.mkfifo('output.parquet')",
        "pathlib.Path('real.parquet').write_bytes(b'x'); os.link('real.parquet', 'output.parquet')",
    ],
    ids=["symlink", "fifo", "hard-link"],
)
def test_an_output_that_is_not_a_plain_file_is_refused(frame, make):
    script = f"import os, pathlib\n{make}\nos._exit(0)\n"

    status, header, payload = run(URL, TOKEN, script, frame)

    assert status == 200
    assert header["ok"] is False
    assert header["error"] == "The cell's output could not be read."
    assert payload == b""


def test_no_process_of_a_run_outlives_it(frame):
    # The audit refuses os.fork by name; a name built at run time passes it.
    escape = """
import os, time
fork = getattr(os, "fo" + "rk")
if fork() == 0:
    os.setsid()
    if fork() == 0:
        while True:
            if fork() == 0:
                time.sleep(600)
                os._exit(0)
            time.sleep(0.2)
    os._exit(0)
time.sleep(1)
"""
    look = """
import os, pathlib
mine = os.getsid(0)
for name in os.listdir("/proc"):
    if not name.isdigit():
        continue
    try:
        owner = os.stat(f"/proc/{name}").st_uid
        stat = pathlib.Path(f"/proc/{name}/stat").read_text()
        state = stat.rsplit(")", 1)[1].split()[0]
    except OSError:
        continue
    if 20000 <= owner < 21000 and state != "Z" and os.getsid(int(name)) != mine:
        print("alive", name, owner)
"""

    first = run(URL, TOKEN, escape, frame, mode="inspect")[1]
    second = run(URL, TOKEN, look, frame, mode="inspect")[1]

    assert first["ok"] is True, first["error"]
    assert second["ok"] is True, second["error"]
    assert "alive" not in second["stdout"]


def test_a_flood_of_output_keeps_only_its_end(frame):
    script = "import sys\nfor i in range(3_000_000):\n    sys.stdout.write(f'{i:099d}\\n')\n"

    _, header, _ = run(URL, TOKEN, script, frame, mode="inspect")

    assert header["ok"] is True, header["error"]
    assert len(header["stdout"]) == 4000
    assert header["stdout"].endswith(f"{2_999_999:099d}\n")


def test_files_written_outside_the_frame_count_against_the_reservation(frame):
    script = """
import pathlib, time
block = b"x" * (256 * 1024**2)
for i in range(16):
    pathlib.Path(f"/tmp/fill-{i}").write_bytes(block)
    time.sleep(0.5)
"""

    _, header, _ = run(URL, TOKEN, script, frame, mode="inspect")

    assert header["ok"] is False
    assert header["error"] == (
        "The script used more than 1.5 GiB of memory and scratch files and was stopped."
    )


def test_a_script_cannot_read_the_ledger_of_other_runs(frame):
    script = """
import pathlib
try:
    pathlib.Path("/tmp/sandbox/ledger.json").read_bytes()
    print("read")
except PermissionError:
    print("denied")
"""

    _, header, _ = run(URL, TOKEN, script, frame, mode="inspect")

    assert header["ok"] is True, header["error"]
    assert header["stdout"].strip() == "denied"


HIDDEN_FILES = {
    "memfd": "import os\nfd = os.memfd_create('fill')\nwrite = lambda b: os.write(fd, b)\n",
    "unlinked": (
        "import io, pathlib\nfh = io.FileIO('held', 'w')\npathlib.Path('held').unlink()\n"
        "write = fh.write\n"
    ),
}


@pytest.mark.parametrize("opening", HIDDEN_FILES.values(), ids=HIDDEN_FILES.keys())
def test_files_no_directory_lists_count_against_the_reservation(frame, opening):
    script = opening + (
        "import time\nblock = b'x' * (256 * 1024**2)\nfor _ in range(7):\n"
        "    write(block)\n    time.sleep(0.5)\ntime.sleep(10)\n"
    )

    _, header, _ = run(URL, TOKEN, script, frame, mode="inspect")

    assert header["ok"] is False
    assert header["error"] == (
        "The script used more than 1.5 GiB of memory and scratch files and was stopped."
    )


def test_a_run_that_creates_too_many_files_is_stopped(frame):
    script = """
import pathlib, time
for i in range(60_000):
    pathlib.Path(f"f{i}").touch()
time.sleep(10)
"""

    _, header, _ = run(URL, TOKEN, script, frame, mode="inspect")

    assert header["ok"] is False
    assert header["error"] == "The script created more than 50,000 files and was stopped."


def test_a_document_is_extracted_with_no_route_out():
    document = Path(__file__).parent / "fixtures" / "documents" / "scanned.pdf"
    content = document.read_bytes()
    body = header_bytes({"mode": "extract", "filename": document.name, "input_bytes": len(content)})

    response = httpx.post(
        f"{URL}/v1/run",
        content=body + content,
        headers={"Authorization": f"Bearer {TOKEN}"},
        timeout=300,
    )

    reader = HeaderReader()
    payload = reader.feed(response.content)
    header = reader.finish()
    assert header["ok"] is True, header["error"]
    assert header["extraction"]["ocr"]["languages"] == ["eng"]
    rows = pq.read_table(io.BytesIO(payload)).to_pylist()
    assert [row["text"] for row in rows] == ["Policy 7: refunds need a receipt."]
