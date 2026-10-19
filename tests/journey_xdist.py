"""Give each journey xdist worker its own Redis DB before Django reads settings."""

import os

import pytest

# ponytail: six Redis DBs, one pair (broker, cache=broker-1) per worker.
# DB 12/11 stay with the load subprocess. More than six workers collide.
_JOURNEY_REDIS_DBS = (15, 10, 8, 6, 4, 2)


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config, parser, args):
    worker = os.environ.get("PYTEST_XDIST_WORKER", "")
    url = os.environ.get("TEST_REDIS_URL", "")
    if not worker.startswith("gw") or not url or not os.environ.get("OVERMIND_JOURNEY_XDIST"):
        return
    index = int(worker.removeprefix("gw"))
    if index >= len(_JOURNEY_REDIS_DBS):
        raise pytest.UsageError(f"Journey workers stop at {len(_JOURNEY_REDIS_DBS)}; got {worker}.")
    os.environ["TEST_REDIS_URL"] = f"{url.rsplit('/', 1)[0]}/{_JOURNEY_REDIS_DBS[index]}"
