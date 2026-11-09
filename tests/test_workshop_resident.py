import asyncio
import base64
import json
import os
import signal
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

import pytest

from overbae.services.datasets.context import CONSUMERS
from workshop_runtime import agent, mailbox, session, skills

pytestmark = pytest.mark.covers("work-once")


def reply(name, arguments):
    return {
        "status": 200,
        "body": {
            "id": "resp_" + uuid4().hex,
            "object": "response",
            "created_at": 1,
            "status": "completed",
            "model": "test",
            "output": [
                {
                    "id": "fc_" + uuid4().hex,
                    "call_id": "call_" + uuid4().hex,
                    "type": "function_call",
                    "name": name,
                    "arguments": json.dumps(arguments),
                    "status": "completed",
                }
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        },
    }


async def drive(root, running, choices):
    delivered = set()
    deadline = asyncio.get_running_loop().time() + 30
    while not running.done():
        if asyncio.get_running_loop().time() > deadline:
            running.cancel()
            raise AssertionError("Resident run failed to settle")
        snapshot = agent.exchange(root, {})
        for request in snapshot["pending"]:
            if request["id"] not in delivered:
                name, arguments = choices.pop(0)
                agent.exchange(root, {"responses": {request["id"]: reply(name, arguments)}})
                delivered.add(request["id"])
        await asyncio.sleep(0.02)
    await running


def test_resident_repairs_in_one_kernel_and_retains_complete_conversation():
    async def trial(root):
        for name, text in skills.files(CONSUMERS).items():
            path = root / "context" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        agent.session.save(
            agent.private(root) / "configuration.json",
            {
                "task": str(uuid4()),
                "model": "test",
                "objective": "Explore the values",
                "runtime_contracts": {
                    "consumers.json": {"decision_train": "fixture-consumer-contract"}
                },
                "source_briefing": {"context.json": {"name": "fixture-pinned-source"}},
            },
        )
        runner = agent.Runner(root)
        running = asyncio.create_task(runner.run())
        await drive(
            root,
            running,
            [
                (
                    "python",
                    {
                        "code": "values=[2,4,6]; print('start-' + 'x'*20000 + '-end')",
                        "skills": ["decision-data"],
                    },
                ),
                ("python", {"code": "sum(values)/0"}),
                (
                    "python",
                    {
                        "code": "from pathlib import Path\nPath('mean.txt').write_text(str(sum(values)/len(values)))"
                    },
                ),
                ("read", {"path": "mean.txt"}),
                (
                    "finish",
                    {
                        "outcome": "blocked",
                        "reason": "No training target was supplied in this fixture.",
                    },
                ),
            ],
        )
        assert (root / "work/mean.txt").read_text() == "4.0"
        retained = json.loads((root / ".agent/state.json").read_text())
        assert retained["events"][1]["error"] == "ZeroDivisionError"
        assert "4.0" in json.dumps(retained["messages"])
        assert json.dumps(retained["messages"]).count("fixture-consumer-contract") == 1
        assert json.dumps(retained["messages"]).count("fixture-pinned-source") == 1
        assert retained["state"] == "blocked"
        first = json.loads(retained["messages"][2]["parts"][0]["content"])
        assert first["compacted"] is True
        assert first["output"]["head"].startswith("start-")
        assert first["output"]["tail"].endswith("-end\n")
        assert "categorical_gold" in first["skills"]["decision-data"]
        receipt = json.loads((root / "work" / first["full_receipt"]).read_text())
        assert "x" * 20000 in receipt["result"]["output"]

    with TemporaryDirectory(prefix="wa-", dir="/tmp") as temporary:
        root = Path(temporary)
        try:
            asyncio.run(trial(root))
        finally:
            health = session.request(root, {"operation": "health"})
            session.request(root, {"operation": "close"})
            os.kill(health["supervisor_pid"], signal.SIGTERM)


def test_resident_restores_pending_python_without_repeating_its_effect():
    async def trial(root):
        configuration = {"task": str(uuid4()), "model": "test", "objective": "Count once"}
        session.save(agent.private(root) / "configuration.json", configuration)
        runner = agent.Runner(root)
        code = "from pathlib import Path\np=Path('count')\np.write_text(str(int(p.read_text())+1) if p.exists() else '1')"
        result = await runner.python({"code": code, "timeout": 10})
        assert result["exit_code"] == 0
        resumed = agent.Runner(root)
        assert (await resumed.python({"code": code, "timeout": 10}))["exit_code"] == 0
        assert (root / "work/count").read_text() == "1"
        with pytest.raises(ValueError, match="conflicts"):
            await resumed.python({"code": "print(42)", "timeout": 10})

    with TemporaryDirectory(prefix="wa-", dir="/tmp") as temporary:
        root = Path(temporary)
        health = session.ensure(root)
        try:
            asyncio.run(trial(root))
        finally:
            session.request(root, {"operation": "close"})
            os.kill(health["supervisor_pid"], signal.SIGTERM)


def test_cancellation_interrupts_active_python_before_its_later_mutation():
    async def trial(root):
        session.save(
            agent.private(root) / "configuration.json",
            {"task": str(uuid4()), "model": "test", "objective": "Cancellation check"},
        )
        runner = agent.Runner(root)
        running = asyncio.create_task(
            runner.python(
                {
                    "code": "import time\nfrom pathlib import Path\ntime.sleep(30)\nPath('should-not-exist').write_text('late mutation')",
                    "timeout": 60,
                }
            )
        )
        deadline = asyncio.get_running_loop().time() + 10
        while not session.request(root, {"operation": "health"})["active"]:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.02)
        agent.exchange(root, {"control": "cancelled"})
        with pytest.raises(agent.StoppedError):
            await asyncio.wait_for(running, 5)
        assert runner.state["state"] == "cancelled"
        while session.request(root, {"operation": "health"})["active"]:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.02)
        assert not (root / "work/should-not-exist").exists()

    with TemporaryDirectory(prefix="wa-", dir="/tmp") as temporary:
        root = Path(temporary)
        health = session.ensure(root)
        try:
            asyncio.run(trial(root))
        finally:
            session.request(root, {"operation": "close"})
            os.kill(health["supervisor_pid"], signal.SIGTERM)


def test_cancellation_escapes_the_model_transport_without_becoming_a_connection_error():
    async def trial(root):
        session.save(
            agent.private(root) / "configuration.json",
            {"task": str(uuid4()), "model": "test", "objective": "Cancellation check"},
        )
        runner = agent.Runner(root)
        running = asyncio.create_task(runner.run())
        deadline = asyncio.get_running_loop().time() + 5
        while not agent.exchange(root, {})["pending"]:
            assert asyncio.get_running_loop().time() < deadline
            await asyncio.sleep(0.02)
        agent.exchange(root, {"control": "cancelled"})
        with pytest.raises(agent.StoppedError):
            await asyncio.wait_for(running, 2)
        assert runner.state["state"] == "cancelled"
        assert not runner.state.get("reason")

    with TemporaryDirectory(prefix="wa-", dir="/tmp") as temporary:
        root = Path(temporary)
        health = session.ensure(root)
        try:
            asyncio.run(trial(root))
        finally:
            session.request(root, {"operation": "close"})
            os.kill(health["supervisor_pid"], signal.SIGTERM)


def test_mailbox_delivers_once_and_rejects_conflicting_recovery(tmp_path):
    identity = str(uuid4())
    directory = mailbox.private(tmp_path)
    mailbox.save(directory / f"request-{identity}.json", {"id": identity, "kind": "model"})
    response = reply("finish", {"outcome": "answered", "reason": "Done"})
    payload = {"responses": {identity: response}, "messages": [{"id": "1", "text": "Next"}]}
    # The transport must work without any site packages or agent imports.
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-m",
            "workshop_runtime.mailbox",
            "--root",
            str(tmp_path),
            "--payload",
            base64.b64encode(json.dumps(payload).encode()).decode(),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(result.stdout)["pending"] == []
    assert mailbox.exchange(tmp_path, payload)["pending"] == []
    assert json.loads((directory / f"response-{identity}.json").read_text()) == response
    with pytest.raises(ValueError, match="conflicts"):
        mailbox.exchange(tmp_path, {"responses": {identity: {"status": 500}}})
    with pytest.raises(ValueError, match="conflicts"):
        mailbox.exchange(tmp_path, {"messages": [{"id": "1", "text": "Changed"}]})
