import ipaddress
import os
import socket
from pathlib import Path

import pytest
from asgiref.sync import SyncToAsync, async_to_sync
from django.db import connections
from fakes.clerk import ClerkAPI
from fakes.ecs import FakeECS
from fakes.http import ScriptedAPI
from fakes.llm import FakeLLM, Network
from fakes.modal import FakeModal, ServingBackend, SftBackend
from fakes.stripe import StripeAPI


def pytest_ignore_collect(collection_path, config):
    if collection_path.name != "journeys" or collection_path.parent != Path(__file__).parent:
        return None
    # Journeys share one broker and Redis DB, so xdist workers would run each other's tasks.
    parallel = hasattr(config, "workerinput") or config.getoption("numprocesses", default=None)
    return True if parallel or not os.environ.get("TEST_REDIS_URL") else None


def drain_stream(response) -> bytes:
    """Collect a streaming response body in a sync test.

    SSE views hand `StreamingHttpResponse` an async iterable so ASGI writes chunks as they
    are produced, which leaves `streaming_content` un-joinable from sync code.
    """
    if not response.streaming:
        return response.content
    content = response.streaming_content
    if not hasattr(content, "__aiter__"):
        return b"".join(content)

    async def _collect():
        return [chunk async for chunk in content]

    return b"".join(async_to_sync(_collect)())


def _loopback(address) -> bool:
    if not isinstance(address, tuple):
        return True
    host = address[0]
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _no_outside_sockets(monkeypatch):
    connect = socket.socket.connect

    def guarded(sock, address):
        if not _loopback(address):
            raise ConnectionRefusedError(f"Tests may not reach {address}.")
        return connect(sock, address)

    monkeypatch.setattr(socket.socket, "connect", guarded)
    for proxy in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.setenv(proxy, "http://127.0.0.1:9")
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


@pytest.fixture(autouse=True)
def fake_llm():
    llm = FakeLLM()
    with Network(llm) as network:
        llm.network = network
        yield llm
    assert not network.refused, f"Unrouted outbound calls: {network.refused}"


@pytest.fixture
def stripe_api(settings, fake_llm) -> StripeAPI:
    api = StripeAPI()
    settings.STRIPE_WEBHOOK_SECRET = api.webhook_secret
    fake_llm.network.vendors.append(api)
    return api


@pytest.fixture
def scripted(fake_llm):
    def install(host: str) -> ScriptedAPI:
        api = ScriptedAPI(host)
        fake_llm.network.vendors.append(api)
        return api

    return install


@pytest.fixture
def ecs(monkeypatch):
    """Run as a hosted ECS task. Archive keys stay in the environment, as they do in
    production, so every AWS call must prove it signs with the task role instead."""
    fake = FakeECS().start()
    for name, value in fake.env().items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("AWS_CONTAINER_CREDENTIALS_RELATIVE_URI", raising=False)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "archive-key")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "archive-secret")
    yield fake
    fake.stop()


@pytest.fixture
def slept(monkeypatch) -> list[float]:
    import asyncio
    import time

    naps: list[float] = []
    monkeypatch.setattr(time, "sleep", naps.append)
    original_sleep = asyncio.sleep

    async def async_nap(delay, result=None):
        if delay > 0:
            naps.append(float(delay))
        return await original_sleep(0, result)

    monkeypatch.setattr(asyncio, "sleep", async_nap)
    return naps


@pytest.fixture
def fake_modal(monkeypatch) -> FakeModal:
    return FakeModal().install(monkeypatch)


@pytest.fixture
def sft(fake_modal) -> SftBackend:
    return SftBackend(fake_modal).install()


@pytest.fixture
def serving(fake_modal) -> ServingBackend:
    return ServingBackend(fake_modal, url="http://inference.test").install()


@pytest.fixture(autouse=True)
def _close_sync_to_async_connections():
    yield
    SyncToAsync.single_thread_executor.submit(connections.close_all).result()


@pytest.fixture(autouse=True)
def _clerk_offline(settings):
    settings.CLERK_API_SECRET_KEY = ""


@pytest.fixture
def clerk(settings, fake_llm) -> ClerkAPI:
    api = ClerkAPI()
    settings.CLERK_API_SECRET_KEY = "sk_test_clerk"
    settings.CLERK_AUTHORIZED_PARTIES = ["http://localhost:5173"]
    fake_llm.network.vendors.append(api)
    return api


@pytest.fixture(autouse=True)
def _openrouter_key(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "offline-test-key")
    # The Cursor SDK dials out from a bridge process the socket guard cannot see.
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)


@pytest.fixture(autouse=True)
def _commercial_billing(settings):
    """Remaining-credit billing is on in tests unless a case clears the key."""
    settings.STRIPE_SECRET_KEY = "sk_test_billing"


EVAL_ROWS = [
    {"input": "q1", "expected_output": "a1"},
    {"input": "q2", "expected_output": "a2"},
]
TRAIN_ROWS = [
    {"messages": [{"role": "user", "content": "q1"}, {"role": "assistant", "content": "a1"}]},
    {"messages": [{"role": "user", "content": "q2"}, {"role": "assistant", "content": "a2"}]},
]


@pytest.fixture(autouse=True)
def _fresh_cache():
    from django.core.cache import cache

    cache.clear()


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    """Every test writes dataset files under its own tmp dir."""
    settings.MEDIA_ROOT = tmp_path / "media"


@pytest.fixture(autouse=True)
def _inline_dataset_tasks(monkeypatch):
    """Landing and runs execute in-process: no worker in tests, and the API's
    202 answers still leave a finished version behind."""
    from overbae.tasks import datasets as dataset_tasks

    for task in (dataset_tasks.land, dataset_tasks.run):
        monkeypatch.setattr(
            task,
            "apply_async",
            lambda kwargs, task_id=None, _t=task, **_: _t.apply(kwargs=kwargs, task_id=task_id),
        )
    # The agent needs Cursor; a test that wants a turn drives the agent module itself.
    # Landing hands the dataset to its first scan, so the stub ends that scan.
    from overbae.services.datasets.notebook import agent

    monkeypatch.setattr(
        dataset_tasks.diagnose,
        "apply_async",
        lambda kwargs, **_: agent.settle(kwargs["dataset_id"]),
    )
    monkeypatch.setattr(dataset_tasks.turn, "apply_async", lambda kwargs, **_: None)


def frozen_dataset(project, rows=None, *, capability=None, name="ds", contract=None, user=None):
    """A dataset whose source landed in-process, so its active version is
    ready. ``rows`` defaults to a two-row table of the requested intent."""
    from overbae.models import Dataset
    from overbae.services.datasets import land
    from overbae.services.datasets.notebook import run as run_svc

    if rows is None:
        rows = TRAIN_ROWS if contract == "train" else EVAL_ROWS
    dataset = Dataset.objects.create(
        project=project, capability=capability, name=name, intent=contract or "pending"
    )
    land.land_rows(dataset, list(rows), user=user)
    run_svc.execute(dataset, user=user)
    dataset.refresh_from_db()
    return dataset


@pytest.fixture(autouse=True)
def _rubric_compiler(fake_llm):
    fake_llm.on_json(
        lambda r: r.schema_name == "_Checklist",
        lambda r: {
            "items": [
                {"id": "criterion_1", "q": "Does the output satisfy the rubric?", "weight": 0.5},
                {"id": "criterion_2", "q": "Is the output free of errors?", "weight": 0.5},
            ],
            "variables": ["input", "output"],
        },
    )


@pytest.fixture
def make_dataset(settings, tmp_path):
    """Land rows and run the chain in-process, so a test gets a version back
    synchronously without a worker. ``cells`` is a list of ``(title, script)``."""

    def _make(
        project,
        rows,
        *,
        name="rows",
        capability=None,
        cells=None,
        run=True,
        user=None,
        source_kind="file",
        target=None,
    ):
        from overbae.models import Dataset
        from overbae.services.datasets import land, lifecycle
        from overbae.services.datasets.notebook import run as run_svc

        dataset = Dataset.objects.create(
            project=project,
            capability=capability,
            name=name,
            source_kind=source_kind,
            intent=target or "pending",
        )
        land.land_rows(dataset, list(rows), user=user)
        for title, script in cells or []:
            lifecycle.add_cell(dataset, title=title, script=script, user=user)
        if run:
            run_svc.execute(dataset, user=user)
        dataset.refresh_from_db()
        return dataset

    return _make
