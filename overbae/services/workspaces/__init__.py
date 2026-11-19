"""Where the assistant runs Python: one workspace for each project, one kernel for each
conversation in it. A provider is one module in this package and one entry in
``PROVIDERS``; nothing outside this package names a provider.

Every provider gives a kernel the same layout: its working directory is its own and
outlives it, and ``data/<name>`` holds the project's versions, read-only. A kernel is
disposable: a provider may end it at any time, and the next cell starts a new one.
"""

from dataclasses import dataclass
from typing import BinaryIO, Protocol

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

DEFAULT_SECONDS = 120
MAX_SECONDS = 600
# A file a kernel wrote, read back for a chart, a table or an image.
MAX_FILE_BYTES = 64 * 1024**2


@dataclass(frozen=True)
class Execution:
    ok: bool
    output: str
    error: str
    truncated: bool
    seconds: float
    # Why the conversation's variables are gone, when this cell started a new kernel
    # after an earlier one ended.
    restarted: str | None = None


class WorkspaceUnavailableError(Exception):
    """The provider cannot serve the call now. Nothing ran."""


class Provider(Protocol):
    def put_data(self, project: str, name: str, source: BinaryIO) -> None:
        """Copy a version into the project's ``data``, once: a name already there is kept."""

    def has_data(self, project: str, name: str) -> bool: ...

    def run(self, project: str, conversation: str, code: str, timeout: int) -> Execution:
        """Run ``code`` in the conversation's kernel, starting one when it has none. A kernel
        that ends during the cell is reported in the result, not raised."""

    def read_file(self, project: str, conversation: str, name: str) -> bytes:
        """A file the kernel wrote in its working directory. A missing, oversized or
        irregular file raises ``FileNotFoundError``."""

    def stop(self, project: str, conversation: str) -> None:
        """End the conversation's kernel, also during a cell. Idempotent."""

    def forget(self, project: str, data: list[str], conversations: list[str]) -> None:
        """Delete these versions and conversations from the workspace: their dataset was
        deleted. Idempotent."""

    def remove(self, project: str) -> None:
        """Delete the whole workspace: its project was deleted. Idempotent."""


PROVIDERS = {
    "native": "overbae.services.workspaces.native.NativeProvider",
    "daytona": "overbae.services.workspaces.daytona.DaytonaProvider",
    "nitro": "overbae.services.workspaces.nitro.NitroProvider",
}


def provider() -> Provider:
    path = PROVIDERS.get(settings.WORKSPACE_PROVIDER)
    if path is None:
        raise ImproperlyConfigured(
            f"WORKSPACE_PROVIDER is {settings.WORKSPACE_PROVIDER!r}; "
            f"set one of {', '.join(PROVIDERS)}."
        )
    return import_string(path)()
