import io
import os
import sys
import uuid

import pytest

from overbae.services.sandbox_sessions import (
    SessionGoneError,
    SessionUnavailableError,
    conformance,
)
from overbae.services.sandbox_sessions.native import NativeProvider

pytestmark = pytest.mark.covers("sandbox-sessions")

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


def test_a_native_session_is_reached_on_its_own_server_and_is_gone_with_it(sandbox_server):
    url, token = sandbox_server
    service, dead = NativeProvider(url, token), "http://127.0.0.1:9"
    session = service.start()
    try:
        assert session.startswith(url)
        reached = NativeProvider(dead, token).execute(
            session, "1 + 1", execution_id="a", timeout=20
        )
        assert reached.output == "2\n"
    finally:
        service.stop(session)
    # Its server is gone while the service answers: so is the session.
    with pytest.raises(SessionGoneError):
        service.execute(session.replace(url, dead), "1", execution_id="b", timeout=5)
    # The whole service is silent: it may come back with the session.
    with pytest.raises(SessionUnavailableError):
        NativeProvider(dead, token).execute(
            dead + "/v1/sessions/x", "1", execution_id="c", timeout=5
        )


@contained
@pytest.mark.parametrize("check", conformance.ISOLATED_CHECKS, ids=lambda check: check.__name__)
def test_native_provider_is_isolated(provider, check):
    check(provider)


@contained
def test_the_native_kernel_is_unprivileged_and_cannot_change_its_inputs(provider):
    session = provider.start()
    try:
        provider.put_file(session, "source.csv", io.BytesIO(b"a,b\n1,2\n"))
        result = provider.execute(
            session,
            "import os\n"
            "assert os.getuid() >= 20000\n"
            "for attempt in (lambda: open('../inputs/source.csv', 'a'),\n"
            "                lambda: open('../inputs/new', 'w'),\n"
            "                lambda: os.listdir('../..')):\n"
            "    try:\n"
            "        attempt()\n"
            "    except OSError:\n"
            "        continue\n"
            "    raise AssertionError('allowed')\n"
            "open('../inputs/source.csv').read()",
            execution_id=uuid.uuid4().hex,
            timeout=20,
        )
        assert result.ok and result.output == "'a,b\\n1,2\\n'\n", result
    finally:
        provider.stop(session)
