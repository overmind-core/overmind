import os
import uuid
from contextlib import ExitStack
from copy import deepcopy
from urllib.parse import urlparse

import pytest
import redis
from celery.contrib.testing.worker import start_worker
from journeys.stack import LiveAPI

from overbae.celery import app
from tests.load.processes import ProcessAPI, ProcessWorker


@pytest.fixture(scope="session", autouse=True)
def isolated_broker():
    url = os.environ["TEST_REDIS_URL"]
    parsed = urlparse(url)
    assert parsed.hostname in ("localhost", "127.0.0.1") and parsed.path in (
        "/12",
        "/13",
    ), "Load tests require local broker 12 or 13; cache 14 and parent worker 15 are reserved."
    redis.from_url(url).flushdb()


@pytest.fixture(autouse=True)
def _inline_dataset_tasks():
    yield


@pytest.fixture(autouse=True)
def _offline_compute_prices():
    yield


@pytest.fixture(autouse=True)
def _offline_operational_provider():
    yield


@pytest.fixture(autouse=True)
def _media_root(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    storages = deepcopy(settings.STORAGES)
    storages["default"]["OPTIONS"]["location"] = str(settings.MEDIA_ROOT)
    settings.STORAGES = storages


@pytest.fixture
def profile_workers(settings, tmp_path):
    with ExitStack() as stack:
        stack.enter_context(ProcessWorker(settings, tmp_path))
        for queue, concurrency in (
            ("control", 2),
            ("io", 4),
            ("io_traces", 4),
            ("batch", 2),
            ("landing", 2),
        ):
            stack.enter_context(
                start_worker(
                    app,
                    pool="threads",
                    concurrency=concurrency,
                    queues=[queue],
                    hostname=f"mixed-{queue}-{uuid.uuid4().hex}",
                    perform_ping_check=False,
                    use_eventloop=False,
                    shutdown_timeout=120,
                )
            )
        yield


@pytest.fixture(scope="session")
def live_api():
    api = LiveAPI().start()
    yield api
    api.stop()


@pytest.fixture
def process_api(transactional_db, settings, tmp_path):
    with ProcessAPI(settings, tmp_path) as api:
        yield api
