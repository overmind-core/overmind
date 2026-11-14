import contextlib
from typing import BinaryIO

import httpx
from django.conf import settings

from overbae.services.sandbox_sessions import (
    Execution,
    SessionGoneError,
    SessionUnavailableError,
)
from sandbox.sessions import REPLY_GRACE_SECONDS

CHUNK_BYTES = 1024**2
# The sandbox waits up to 120 s for memory before it answers that it has none.
START_SECONDS = 150
TRANSFER_SECONDS = 300
# The server ends a kernel that is silent past its own grace; this client waits out that.
CLIENT_GRACE_SECONDS = REPLY_GRACE_SECONDS + 15


class _UnreachableError(Exception):
    pass


class _SilentError(SessionUnavailableError):
    pass


class NativeProvider:
    """Sessions in Overmind's own ``sandbox`` service. A session is named by its URL on the
    server that holds it, so any number of servers can sit behind ``SANDBOX_URL``."""

    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = (url or settings.SANDBOX_URL).rstrip("/")
        self.headers = {"Authorization": f"Bearer {token or settings.SANDBOX_TOKEN}"}

    def _request(self, method: str, url: str, *, timeout: float, **kwargs) -> httpx.Response:
        try:
            response = httpx.request(
                method,
                url,
                headers=self.headers,
                timeout=httpx.Timeout(timeout, connect=5),
                **kwargs,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            raise _UnreachableError from exc
        except httpx.TransportError as exc:
            raise _SilentError(f"The sandbox did not answer: {exc}") from exc
        if response.status_code == 409 or response.status_code >= 500:
            reason = _body(response).get("error") or f"HTTP {response.status_code}"
            raise SessionUnavailableError(reason)
        return response

    def _alive(self) -> bool:
        try:
            return httpx.get(f"{self.url}/health", timeout=5).is_success
        except httpx.HTTPError:
            return False

    def _call(self, method: str, session: str, path: str = "", **kwargs) -> httpx.Response:
        """A call to the server that holds the session."""
        if not session.startswith(("http://", "https://")):
            raise SessionGoneError("This session belongs to another provider.")
        try:
            response = self._request(method, session + path, **kwargs)
        except _UnreachableError:
            # Sessions live in their server's memory. A server that is gone while the
            # service answers took its sessions along; a service that does not answer may
            # come back with them.
            if not session.startswith(self.url) and self._alive():
                raise SessionGoneError("The sandbox server of this session is gone.") from None
            raise SessionUnavailableError("The sandbox did not answer.") from None
        if response.status_code == 404:
            reason = _body(response).get("reason")
            if reason is None and method == "GET":
                raise FileNotFoundError(path)
            raise SessionGoneError(reason or "The session does not exist.")
        response.raise_for_status()
        return response

    def start(self) -> str:
        try:
            response = self._request("POST", f"{self.url}/v1/sessions", timeout=START_SECONDS)
        except _UnreachableError:
            raise SessionUnavailableError("The sandbox did not answer.") from None
        response.raise_for_status()
        started = response.json()
        return f"{started['url'] or self.url}/v1/sessions/{started['id']}"

    def execute(self, session: str, code: str, *, execution_id: str, timeout: int) -> Execution:
        try:
            reply = self._call(
                "POST",
                session,
                "/execute",
                json={"id": execution_id, "code": code, "timeout": timeout},
                timeout=timeout + CLIENT_GRACE_SECONDS,
            )
        except _SilentError:
            # Whether the cell ran is unknown: no later cell can build on this kernel.
            with contextlib.suppress(SessionUnavailableError):
                self.stop(session)
            raise SessionGoneError(
                "The sandbox did not answer for the cell and the session was closed."
            ) from None
        return Execution(**reply.json())

    def put_file(self, session: str, name: str, source: BinaryIO) -> None:
        chunks = iter(lambda: source.read(CHUNK_BYTES), b"")
        self._call("PUT", session, f"/files/{name}", content=chunks, timeout=TRANSFER_SECONDS)

    def get_file(self, session: str, name: str) -> bytes:
        return self._call("GET", session, f"/files/{name}", timeout=TRANSFER_SECONDS).content

    def stop(self, session: str) -> None:
        with contextlib.suppress(SessionGoneError):
            self._call("DELETE", session, timeout=30)


def _body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
