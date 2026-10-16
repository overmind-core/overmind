import pytest
from journeys.stack import LiveAPI


@pytest.fixture(scope="session")
def live_api():
    api = LiveAPI().start()
    yield api
    api.stop()
