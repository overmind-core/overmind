import json
import os
import signal
import subprocess
import sys
import time
from contextlib import suppress
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

from workshop_runtime.session import ensure, request

pytestmark = pytest.mark.covers("work-once")


@pytest.fixture
def session():
    # Unix socket paths are short even when pytest's temporary directory is long.
    directory = TemporaryDirectory(prefix="wk-", dir="/tmp")
    root = Path(directory.name)
    health = ensure(root)
    yield root
    current = request(root, {"operation": "health"})
    if current["active"]:
        request(root, {"operation": "interrupt", "execution": current["active"]})
        settle(root, current["active"])
    request(root, {"operation": "close"})
    os.kill(current["supervisor_pid"], signal.SIGTERM)
    if current["supervisor_pid"] != health["supervisor_pid"]:
        with suppress(ProcessLookupError):
            os.kill(health["supervisor_pid"], signal.SIGTERM)
    directory.cleanup()


def settle(root, execution):
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        result = request(root, {"operation": "status", "execution": execution})
        health = request(root, {"operation": "health"})
        if result["state"] in {"completed", "unknown"} and health["active"] is None:
            return result
        time.sleep(0.05)
    raise AssertionError("Execution did not settle")


def execute(root, code):
    execution = str(uuid4())
    request(root, {"operation": "submit", "execution": execution, "code": code})
    return settle(root, execution)


def test_session_repairs_errors_retains_values_and_replays_files_cleanly(session):
    assert execute(session, "values = [2, 4, 6]")["exit_code"] == 0
    error = execute(session, "sum(values) / 0")
    assert error["exit_code"] == 1 and "ZeroDivisionError" in error["output"]
    result = execute(session, "sum(values) / len(values)")
    assert "4.0" in result["output"]
    recipe = session / "work/recipe.py"
    recipe.write_text(
        "import json\nfrom pathlib import Path\nrows=json.loads(Path('input.json').read_text())\nPath('result.json').write_text(json.dumps(sorted(x*2 for x in rows)))\n"
    )
    (session / "work/input.json").write_text("[3, 1, 2]")
    assert execute(session, "exec(open('recipe.py').read())")["exit_code"] == 0
    before = (session / "work/result.json").read_bytes()
    request(session, {"operation": "reset"})
    assert execute(session, "values")["error"] == "NameError"
    subprocess.run([sys.executable, str(recipe)], cwd=recipe.parent, check=True)
    assert json.loads(before) == [2, 4, 6]
    assert (session / "work/result.json").read_bytes() == before


def test_session_recovers_lost_reply_without_repeating_code(session):
    execution = str(uuid4())
    command = {
        "operation": "submit",
        "execution": execution,
        "code": "from pathlib import Path\np=Path('count')\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')",
    }
    request(session, command)
    settle(session, execution)
    repeated = request(session, command)
    assert repeated["state"] == "completed"
    assert (session / "work/count").read_text() == "1"
    with pytest.raises(ValueError, match="conflicts"):
        request(session, {**command, "code": "print(42)"})


def test_supervisor_restart_fences_unknown_execution_and_reset_restores_execution(session):
    execution = str(uuid4())
    request(
        session,
        {"operation": "submit", "execution": execution, "code": "import time\ntime.sleep(60)"},
    )
    health = request(session, {"operation": "health"})
    os.kill(health["supervisor_pid"], signal.SIGKILL)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            request(session, {"operation": "health"})
        except OSError:
            break
        time.sleep(0.05)
    recovered = ensure(session)
    assert recovered["fenced"]
    assert request(session, {"operation": "status", "execution": execution})["state"] == "unknown"
    with pytest.raises(ValueError, match="reset"):
        execute(session, "print(1)")
    reset = request(session, {"operation": "reset"})
    assert reset["generation"] != health["generation"]
    assert execute(session, "6 * 7")["exit_code"] == 0


def test_interrupt_returns_an_error_and_keeps_the_kernel_usable(session):
    execution = str(uuid4())
    request(
        session,
        {"operation": "submit", "execution": execution, "code": "import time\ntime.sleep(60)"},
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        result = request(session, {"operation": "status", "execution": execution})
        if result.get("message_id"):
            break
        time.sleep(0.05)
    request(session, {"operation": "interrupt", "execution": execution})
    result = settle(session, execution)
    assert result["error"] == "KeyboardInterrupt"
    assert execute(session, "print(42)")["exit_code"] == 0
