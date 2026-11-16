import io
import os
import sys

import pytest
from starlette.testclient import TestClient

from overbae.services.workspaces import conformance
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
