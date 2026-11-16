import io
import json
import os
import sys
import uuid

import pytest
from starlette.testclient import TestClient

from overbae.services.workspaces import WorkspaceUnavailableError, conformance
from overbae.services.workspaces.daytona import DaytonaProvider, snapshot_name
from overbae.services.workspaces.native import NativeProvider
from sandbox import server

pytestmark = pytest.mark.covers("workspaces")

CONTAINED = sys.platform == "darwin" or os.environ.get("SANDBOX_CONTAINER_TESTS") == "1"
contained = pytest.mark.skipif(
    not CONTAINED, reason="Requires the root sandbox container on an internal network"
)


@pytest.fixture(scope="module")
def provider(sandbox_server):
    return NativeProvider(*sandbox_server)


@pytest.mark.parametrize("check", conformance.CHECKS, ids=lambda check: check.__name__)
def test_native_provider_meets_the_contract(provider, check):
    check(provider)


@contained
@pytest.mark.parametrize("check", conformance.ISOLATED_CHECKS, ids=lambda check: check.__name__)
def test_native_provider_is_isolated(provider, check):
    check(provider)


@contained
def test_a_native_kernel_is_unprivileged_and_cannot_change_its_data(provider):
    provider.put_data("p1", "source.csv", io.BytesIO(b"a,b\n1,2\n"))
    try:
        result = provider.run(
            "p1",
            "c1",
            "import os\n"
            "assert os.getuid() >= 20000\n"
            "for attempt in (lambda: open('data/source.csv', 'a'),\n"
            "                lambda: open('data/new', 'w'),\n"
            "                lambda: os.listdir('../..')):\n"
            "    try:\n"
            "        attempt()\n"
            "    except OSError:\n"
            "        continue\n"
            "    raise AssertionError('allowed')\n"
            "open('data/source.csv').read()",
            20,
        )
        assert result.ok and result.output == "'a,b\\n1,2\\n'\n", result
    finally:
        provider.stop("p1", "c1")


def test_a_sandbox_without_workspaces_answers_reads_without_creating_them(tmp_path, monkeypatch):
    blocked = tmp_path / "file"
    blocked.write_text("")
    monkeypatch.setenv("SANDBOX_WORKSPACES", str(blocked / "workspaces"))
    monkeypatch.setenv("SANDBOX_TOKEN", "t")
    client = TestClient(server.app, headers={"Authorization": "Bearer t"})
    assert client.head("/v1/workspaces/p1/data/source.csv").status_code == 404
    assert client.get("/v1/workspaces/p1/kernels/c1/files/out.csv").status_code == 404
    assert client.delete("/v1/workspaces/p1").status_code == 204


JSON = {"content-type": "application/json"}


def sandbox(project, snapshot=None):
    return {
        "id": f"s-{project}",
        "organizationId": "o1",
        "name": f"s-{project}",
        "user": "daytona",
        "env": {},
        "labels": {"overmind": "workspace", "project": project},
        "public": False,
        "networkBlockAll": True,
        "kvm": False,
        "target": "eu",
        "cpu": 2,
        "gpu": 0,
        "memory": 4,
        "disk": 5,
        "state": "started",
        "snapshot": snapshot or snapshot_name(),
        "toolboxProxyUrl": "https://proxy.app.daytona.io/toolbox",
    }


class FakeDaytona:
    """Daytona's control plane with the given sandboxes, and the files in their toolbox."""

    def __init__(self, scripted, *boxes):
        self.boxes = {box["id"]: box for box in boxes}
        self.files: set[str] = set()
        self.commands: list[str] = []
        self.exit_code = 0
        self.upload_fails = False
        self.control = scripted("https://app.daytona.io").then(self._control)
        scripted("https://proxy.app.daytona.io").then(self._toolbox)

    def _control(self, call):
        box = self.boxes.get(call.path.rsplit("/", 1)[-1])
        if box is not None:
            if call.method == "DELETE":
                box["state"] = "destroying"
            return 200, JSON, json.dumps(box).encode()
        items = list(self.boxes.values())
        page = {"items": items, "total": len(items), "page": 1, "totalPages": 1}
        return 200, JSON, json.dumps(page).encode()

    def _toolbox(self, call):
        action = call.path.split("/toolbox/", 1)[1].split("/", 1)[1]
        if action == "files/info":
            if call.params["path"] in self.files:
                info = {"name": "f", "isDir": False, "size": 1, "mode": "-r--r--r--"}
                info |= {"modTime": "", "permissions": "0444", "owner": "d", "group": "d"}
                return 200, JSON, json.dumps(info).encode()
            return 404, JSON, b'{"message": "not found", "statusCode": 404}'
        if action == "files/bulk-upload":
            # The bytes that arrived are on disk under the name sent, whole or not.
            target = call.body.split(b"\r\n\r\n", 1)[1].split(b"\r\n", 1)[0].decode()
            self.files.add(target)
            return (500, JSON, b'{"message": "reset"}') if self.upload_fails else (200, JSON, b"{}")
        if action == "files/move":
            self.files.discard(call.params["source"])
            self.files.add(call.params["destination"])
        if action == "process/execute":
            self.commands.append(call.json["command"])
            return 200, JSON, json.dumps({"exitCode": self.exit_code, "result": "denied"}).encode()
        return 200, JSON, b"{}"


@pytest.fixture
def daytona(db, settings):
    settings.DAYTONA_API_KEY = "test"
    return DaytonaProvider()


def test_a_daytona_read_or_forget_creates_no_workspace(daytona, scripted):
    fake = FakeDaytona(scripted)
    project = uuid.uuid4().hex
    assert not daytona.has_data(project, "a.parquet")
    daytona.forget(project, ["a.parquet"], ["c1"])
    with pytest.raises(FileNotFoundError):
        daytona.read_file(project, "c1", "chart.png")
    assert {call.method for call in fake.control.calls} == {"GET"}


def test_a_daytona_workspace_that_outlasts_its_deletion_is_reported(daytona, scripted, slept):
    project = uuid.uuid4().hex
    fake = FakeDaytona(scripted, sandbox(project))
    fake.control.steps[0] = lambda call: (
        (200, JSON, json.dumps(sandbox(project)).encode())
        if call.method == "DELETE"
        else fake._control(call)
    )
    with pytest.raises(WorkspaceUnavailableError):
        daytona.remove(project)
    assert any(call.method == "DELETE" for call in fake.control.calls)


def test_a_daytona_copy_that_breaks_off_is_not_taken_as_copied(daytona, scripted):
    project = uuid.uuid4().hex
    fake = FakeDaytona(scripted, sandbox(project))
    fake.upload_fails = True
    with pytest.raises(WorkspaceUnavailableError):
        daytona.put_data(project, "a.parquet", io.BytesIO(b"rows"))
    assert not daytona.has_data(project, "a.parquet")
    fake.upload_fails = False
    daytona.put_data(project, "a.parquet", io.BytesIO(b"rows"))
    assert daytona.has_data(project, "a.parquet")


def test_a_daytona_forget_that_deletes_nothing_is_reported(daytona, scripted):
    project = uuid.uuid4().hex
    fake = FakeDaytona(scripted, sandbox(project))
    fake.exit_code = 1
    with pytest.raises(WorkspaceUnavailableError):
        daytona.forget(project, ["a.parquet"], [])


def test_a_daytona_sandbox_from_another_recipe_is_replaced(daytona, scripted):
    project = uuid.uuid4().hex
    fake = FakeDaytona(scripted, sandbox(project, "overmind-workspace-000000000000"))
    assert not daytona.has_data(project, "a.parquet")
    assert fake.boxes[f"s-{project}"]["state"] == "destroying"
