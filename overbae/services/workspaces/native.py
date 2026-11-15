from typing import BinaryIO

import httpx
from django.conf import settings

from overbae.services.workspaces import Execution, WorkspaceUnavailableError

CHUNK_BYTES = 1024**2
# The sandbox waits for memory before it starts a kernel; a cell may run to its deadline.
GRACE_SECONDS = 60
TRANSFER_SECONDS = 300


class NativeProvider:
    """Workspaces in Overmind's own ``sandbox`` service: folders on its volume, kernels as
    confined processes. It holds them in one server's memory and disk, so a deployment
    runs one sandbox server."""

    def __init__(self, url: str | None = None, token: str | None = None):
        self.url = (url or settings.SANDBOX_URL).rstrip("/") + "/v1/workspaces"
        self.headers = {"Authorization": f"Bearer {token or settings.SANDBOX_TOKEN}"}

    def _request(self, method: str, path: str, *, timeout: float, **kwargs) -> httpx.Response:
        try:
            response = httpx.request(
                method,
                self.url + path,
                headers=self.headers,
                timeout=httpx.Timeout(timeout, connect=5),
                **kwargs,
            )
        except httpx.HTTPError as exc:
            raise WorkspaceUnavailableError(f"The sandbox did not answer: {exc}") from exc
        if response.status_code >= 500:
            reason = _body(response).get("error") or f"HTTP {response.status_code}"
            raise WorkspaceUnavailableError(reason)
        return response

    def has_data(self, project: str, name: str) -> bool:
        response = self._request("HEAD", f"/{project}/data/{name}", timeout=30)
        return response.status_code == 204

    def put_data(self, project: str, name: str, source: BinaryIO) -> None:
        if self.has_data(project, name):
            return
        chunks = iter(lambda: source.read(CHUNK_BYTES), b"")
        self._request(
            "PUT", f"/{project}/data/{name}", content=chunks, timeout=TRANSFER_SECONDS
        ).raise_for_status()

    def run(self, project: str, conversation: str, code: str, timeout: int) -> Execution:
        response = self._request(
            "POST",
            f"/{project}/kernels/{conversation}/run",
            json={"code": code, "timeout": timeout},
            timeout=timeout + GRACE_SECONDS,
        )
        response.raise_for_status()
        return Execution(**response.json())

    def read_file(self, project: str, conversation: str, name: str) -> bytes:
        response = self._request(
            "GET", f"/{project}/kernels/{conversation}/files/{name}", timeout=TRANSFER_SECONDS
        )
        if response.status_code in (400, 404):
            raise FileNotFoundError(name)
        response.raise_for_status()
        return response.content

    def stop(self, project: str, conversation: str) -> None:
        self._request("DELETE", f"/{project}/kernels/{conversation}", timeout=30)

    def forget(self, project: str, data: list[str], conversations: list[str]) -> None:
        self._request(
            "POST",
            f"/{project}/forget",
            json={"data": data, "conversations": conversations},
            timeout=60,
        ).raise_for_status()

    def remove(self, project: str) -> None:
        self._request("DELETE", f"/{project}", timeout=60).raise_for_status()


def _body(response: httpx.Response) -> dict:
    try:
        body = response.json()
    except ValueError:
        return {}
    return body if isinstance(body, dict) else {}
