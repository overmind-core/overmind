"""One contract for stateful code execution. A provider is one module in this package and
one entry in ``PROVIDERS``; nothing outside this package names a provider.

Every provider gives a session the same layout: the kernel's working directory is
writable, and files the caller puts are read-only at ``../inputs/<name>``.
"""

from dataclasses import dataclass
from typing import BinaryIO, Protocol

from django.conf import settings
from django.utils.module_loading import import_string


@dataclass(frozen=True)
class Execution:
    ok: bool
    output: str
    error: str
    truncated: bool
    seconds: float


class SessionGoneError(Exception):
    """The session no longer exists. Its kernel state is lost; start a new one."""


class SessionUnavailableError(Exception):
    """The provider cannot serve the call now. The session, if there is one, is as it was."""


class SessionProvider(Protocol):
    def start(self) -> str: ...

    def execute(self, session: str, code: str, *, execution_id: str, timeout: int) -> Execution:
        """Run ``code`` in the session's namespace. The same ``execution_id`` returns the
        first result and never runs twice. A cell that gets no answer ends the session:
        whether it ran is unknown, so the caller sees ``SessionGoneError``."""

    def put_file(self, session: str, name: str, source: BinaryIO) -> None: ...

    def get_file(self, session: str, name: str) -> bytes:
        """A file the kernel wrote in its working directory. A missing, oversized or
        irregular file raises ``FileNotFoundError``."""

    def stop(self, session: str) -> None:
        """Idempotent: stopping a session that is gone succeeds."""


PROVIDERS = {
    "native": "overbae.services.sandbox_sessions.native.NativeProvider",
    "daytona": "overbae.services.sandbox_sessions.daytona.DaytonaProvider",
}


def provider() -> SessionProvider:
    return import_string(PROVIDERS[settings.SANDBOX_SESSION_PROVIDER])()
