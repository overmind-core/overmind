import contextlib
import hashlib
import json
import logging
import shutil
import tempfile
import threading
import time
import tomllib
import uuid
from functools import cache
from pathlib import Path
from typing import BinaryIO

from daytona import (
    CreateSandboxFromSnapshotParams,
    CreateSnapshotParams,
    Daytona,
    DaytonaConfig,
    DaytonaError,
    DaytonaNotFoundError,
    DaytonaTimeoutError,
    Image,
    ListSandboxesQuery,
    Resources,
    Sandbox,
)
from django.conf import settings
from django.db import connection, transaction

from overbae.services.workspaces import (
    MAX_FILE_BYTES,
    Execution,
    WorkspaceUnavailableError,
)
from sandbox.kernel import __file__ as kernel_file
from sandbox.workspaces import NAME, RESTARTED

logger = logging.getLogger(__name__)

# The libraries the native sandbox promises a package, at the versions the app locks.
LIBRARIES = (
    "beautifulsoup4",
    "datasketch",
    "duckdb",
    "emoji",
    "ftfy",
    "jellyfish",
    "jsonschema",
    "langcodes",
    "langdetect",
    "levenshtein",
    "lxml",
    "markdownify",
    "matplotlib",
    "numpy",
    "pandas",
    "pillow",
    "pyarrow",
    "python-dateutil",
    "pytz",
    "pyyaml",
    "rapidfuzz",
    "regex",
    "scikit-learn",
    "scipy",
    "simhash",
    "tiktoken",
    "unidecode",
    "wordfreq",
)
FAILED_BUILD = ("error", "build_failed")
GONE = ("destroying", "destroyed")
PYTHON = "3.13"
TOKENIZER_ENCODINGS = ("cl100k_base", "o200k_base")
KERNEL_PATH = "/opt/overmind/kernel.py"
TOKENIZERS = "/opt/overmind/tiktoken"
RESOURCES = Resources(cpu=2, memory=4, disk=5)
KERNEL = Path(kernel_file)
WORKSPACE = "/workspace"
STOP_MINUTES = 15
ARCHIVE_MINUTES = 60
# A workspace holds copies of customer data: one unused for this long is deleted, files
# and all. The next cell creates it again and copies in what it reads.
DELETE_MINUTES = 7 * 24 * 60
START_SECONDS = 120
# A cell's own deadline is the kernel's; this one catches a kernel stuck outside Python.
GRACE_SECONDS = 30
# A known sandbox is not asked for its state again within this time.
FRESH_SECONDS = 60
# Loads the kernel's cell runner into a new context, from the snapshot.
SETUP = (
    "import importlib.util as _overmind_util, json as _overmind_json, signal as _overmind_signal\n"
    f"_overmind_spec = _overmind_util.spec_from_file_location('_overmind_kernel', {KERNEL_PATH!r})\n"
    "_overmind = _overmind_util.module_from_spec(_overmind_spec)\n"
    "_overmind_spec.loader.exec_module(_overmind)\n"
    "_overmind_signal.signal(_overmind_signal.SIGALRM, _overmind._interrupt)\n"
    # The same limits as a native kernel: its own folder writable, the project's data
    # read-only, and no socket but a Unix one.
    "import os as _overmind_os, tempfile as _overmind_tempfile\n"
    "for _overmind_name in ('HOME', 'TMPDIR', 'MPLCONFIGDIR'):\n"
    "    _overmind_os.environ[_overmind_name] = _overmind_os.getcwd()\n"
    "_overmind_tempfile.tempdir = None\n"
    f"_overmind.confine(_overmind_os.getcwd(), readable=({WORKSPACE + '/data'!r},))\n"
    "import matplotlib\n"
    "matplotlib.use('Agg')\n"
    # Last: a cell needs the counter, so a context whose setup stopped early runs nothing.
    "_overmind_cells = 0"
)
LOST = (
    "The kernel ended during the cell: it ran out of memory or stopped answering. "
    "Variables are gone, files remain."
)
# Marks the runner's reply in the context's output, which a cell can also write to directly.
REPLY = "__overmind_reply__:"
UNREAD = "The cell ran, but its result did not come back. Variables remain."


@cache
def _requirements() -> tuple[str, ...]:
    lock = tomllib.loads((KERNEL.parent.parent / "uv.lock").read_text())
    versions = {package["name"]: package["version"] for package in lock["package"]}
    return tuple(f"{name}=={versions[name]}" for name in LIBRARIES)


def _recipe() -> tuple[Image, str]:
    """The snapshot's image, and everything that decides what is in it."""
    cache_tokenizers = (
        'python -c "import tiktoken; [tiktoken.get_encoding(name) for name in '
        f'{TOKENIZER_ENCODINGS!r}]"'
    )
    image = (
        Image.debian_slim(PYTHON)
        .pip_install(*_requirements())
        .env({"TIKTOKEN_CACHE_DIR": TOKENIZERS})
        .run_commands(cache_tokenizers)
        .add_local_file(KERNEL, KERNEL_PATH)
    )
    parts = (PYTHON, *_requirements(), TOKENIZERS, cache_tokenizers, KERNEL_PATH, repr(RESOURCES))
    return image, "\n".join(parts)


@cache
def snapshot_name() -> str:
    """Names the kernel and the whole recipe, so a change to either selects a new snapshot."""
    digest = hashlib.sha256(KERNEL.read_bytes() + _recipe()[1].encode())
    return f"overmind-workspace-{digest.hexdigest()[:12]}"


def _client() -> Daytona:
    return Daytona(DaytonaConfig(api_key=settings.DAYTONA_API_KEY, otel_enabled=False))


def build_snapshot(on_logs) -> str:
    """Build the snapshot sessions start from, unless this recipe already has a usable one."""
    client, name = _client(), snapshot_name()
    with contextlib.suppress(DaytonaNotFoundError):
        existing = client.snapshot.get(name)
        if getattr(existing.state, "value", existing.state) not in FAILED_BUILD:
            return name
        # A build that failed keeps the name: remove it and build again.
        client.snapshot.delete(existing)
    client.snapshot.create(
        CreateSnapshotParams(name=name, image=_recipe()[0], resources=RESOURCES),
        on_logs=on_logs,
    )
    return name


def _cell(code: str, timeout: int) -> str:
    """The call that runs ``code`` through the kernel's own runner: its output bound, its
    deadline and its reply are the same as in the native sandbox."""
    return (
        "_overmind_cells += 1\n"
        f"print({REPLY!r} + _overmind_json.dumps(_overmind.run("
        f"{code!r}, globals(), {timeout}, f'<cell {{_overmind_cells}}>')))"
    )


class DaytonaProvider:
    """A Daytona sandbox for each project, stopped when idle and archived later; its files
    survive both. A conversation's kernel is an interpreter context in it, which a stop
    ends."""

    _lock = threading.Lock()
    # Sandbox of each project, and when its state was last read.
    _sandboxes: dict[str, tuple[Sandbox, float]] = {}
    _contexts: dict[tuple[str, str], object] = {}

    def __init__(self):
        self.client = _client()

    def _create(self, project: str) -> Sandbox:
        # One sandbox for each project, whichever worker asks first.
        with transaction.atomic(), connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", [f"workspace:{project}"])
            found = self._find(project)
            if found is not None:
                return found
            return self.client.create(
                CreateSandboxFromSnapshotParams(
                    snapshot=snapshot_name(),
                    labels=_labels(project),
                    network_block_all=True,
                    public=False,
                    ephemeral=False,
                    auto_stop_interval=STOP_MINUTES,
                    auto_archive_interval=ARCHIVE_MINUTES,
                    auto_delete_interval=DELETE_MINUTES,
                ),
                timeout=START_SECONDS,
            )

    def _find(self, project: str) -> Sandbox | None:
        found = self.client.list(ListSandboxesQuery(labels=_labels(project)))
        live = next((item for item in found if _state(item) not in GONE), None)
        return None if live is None else self.client.get(live.id)

    def _sandbox(self, project: str, *, start: bool = True, create: bool = True) -> Sandbox | None:
        """The project's started sandbox. Without ``create`` a project that has none gets
        none; without ``start`` one that is not running is not started either."""
        with self._lock:
            known = self._sandboxes.get(project)
        if known is not None and time.monotonic() - known[1] < FRESH_SECONDS:
            return known[0]
        try:
            sandbox = None
            if known is not None:
                sandbox = known[0]
                try:
                    sandbox.refresh_data()
                except DaytonaNotFoundError:
                    # Deleted since: by its project's removal, or unused for a week.
                    self._forget(project)
                    sandbox = None
            if sandbox is None:
                sandbox = self._find(project)
            if sandbox is not None and _state(sandbox) in FAILED_BUILD:
                if sandbox.recoverable:
                    if not start:
                        return None
                    # It comes back with its files.
                    sandbox.recover(timeout=START_SECONDS)
                else:
                    # Its files are lost with it: the next cells copy versions in again.
                    self.client.delete(sandbox)
                    self._forget(project)
                    sandbox = None
            if sandbox is None:
                if not (start and create):
                    return None
                sandbox = self._create(project)
            elif _state(sandbox) != "started":
                if not start:
                    return None
                sandbox.start(timeout=START_SECONDS)
        except DaytonaError as exc:
            self._forget(project)
            logger.warning("Daytona workspace for %s is not available: %s", project, exc)
            raise WorkspaceUnavailableError("The workspace provider has no room now.") from exc
        with self._lock:
            self._sandboxes[project] = (sandbox, time.monotonic())
        return sandbox

    def _forget(self, project: str) -> None:
        with self._lock:
            self._sandboxes.pop(project, None)
            for key in [key for key in self._contexts if key[0] == project]:
                del self._contexts[key]

    def has_data(self, project: str, name: str) -> bool:
        sandbox = self._sandbox(project, create=False)
        if sandbox is None:
            return False
        try:
            sandbox.fs.get_file_info(f"{WORKSPACE}/data/{name}")
        except DaytonaNotFoundError:
            return False
        except DaytonaError as exc:
            raise WorkspaceUnavailableError(str(exc)) from exc
        return True

    def put_data(self, project: str, name: str, source: BinaryIO) -> None:
        if not NAME.fullmatch(name):
            raise ValueError("Invalid file name.")
        if self.has_data(project, name):
            return
        sandbox = self._sandbox(project)
        path = f"{WORKSPACE}/data/{name}"
        # Under its name only when whole: has_data takes a name that is there as copied.
        partial = f"{WORKSPACE}/data/.{uuid.uuid4().hex}"
        with tempfile.NamedTemporaryFile() as local:
            shutil.copyfileobj(source, local)
            local.flush()
            try:
                sandbox.fs.create_folder(f"{WORKSPACE}/data", "755")
                sandbox.fs.upload_file(local.name, partial)
                sandbox.fs.set_file_permissions(partial, mode="444")
                sandbox.fs.move_files(partial, path)
            except DaytonaError as exc:
                self._forget(project)
                with contextlib.suppress(DaytonaError):
                    sandbox.fs.delete_file(partial)
                raise WorkspaceUnavailableError(str(exc)) from exc

    def _context(self, project: str, conversation: str, sandbox: Sandbox):
        """The conversation's context, and why its variables are gone when a new one
        replaced an earlier one."""
        with self._lock:
            known = self._contexts.get((project, conversation))
        if known is not None:
            return known, None
        work = f"{WORKSPACE}/conversations/{conversation}"
        found = next((c for c in sandbox.code_interpreter.list_contexts() if c.cwd == work), None)
        restarted = None
        if found is None:
            existed = sandbox.process.exec(f"test -d {work}", timeout=30).exit_code == 0
            restarted = RESTARTED if existed else None
            sandbox.process.exec(
                f"mkdir -p {WORKSPACE}/data {work} && ln -sfn ../../data {work}/data",
                timeout=30,
            )
            found = sandbox.code_interpreter.create_context(cwd=work)
            setup = sandbox.code_interpreter.run_code(SETUP, context=found, timeout=60)
            if setup.error:
                with contextlib.suppress(DaytonaError):
                    sandbox.code_interpreter.delete_context(found)
                raise WorkspaceUnavailableError(f"The kernel did not start: {setup.error.value}")
        with self._lock:
            self._contexts[(project, conversation)] = found
        return found, restarted

    def _end(self, project: str, conversation: str, sandbox: Sandbox, context) -> None:
        with self._lock:
            self._contexts.pop((project, conversation), None)
        if context is None:
            return
        with contextlib.suppress(DaytonaError):
            sandbox.code_interpreter.delete_context(context)

    def run(self, project: str, conversation: str, code: str, timeout: int) -> Execution:
        for attempt in range(2):
            sandbox, context = self._sandbox(project), None
            try:
                context, restarted = self._context(project, conversation, sandbox)
                result = sandbox.code_interpreter.run_code(
                    _cell(code, timeout), context=context, timeout=timeout + GRACE_SECONDS
                )
            except DaytonaTimeoutError:
                self._end(project, conversation, sandbox, context)
                return Execution(False, "", LOST, False, float(timeout))
            except DaytonaError as exc:
                text = str(exc)
                if "worker process ended" in text:
                    self._end(project, conversation, sandbox, context)
                    return Execution(False, "", LOST, False, 0.0)
                # A stopped sandbox, or a context its stop ended: look again, once.
                self._forget(project)
                if attempt:
                    raise WorkspaceUnavailableError(text) from exc
                continue
            if result.error:
                # Raised by the runner itself, not the cell: the context is not usable.
                self._end(project, conversation, sandbox, context)
                return Execution(
                    False, "", f"{result.error.name}: {result.error.value}", False, 0.0
                )
            reply = _reply(result.stdout)
            if reply is None:
                return Execution(False, "", UNREAD, False, 0.0, restarted)
            return Execution(**reply, restarted=restarted)
        raise WorkspaceUnavailableError("The workspace did not answer.")

    def read_file(self, project: str, conversation: str, name: str) -> bytes:
        if not NAME.fullmatch(name):
            raise FileNotFoundError(name)
        sandbox = self._sandbox(project, create=False)
        if sandbox is None:
            raise FileNotFoundError(name)
        # The kernel controls the name: it can be a link out of its folder, which the
        # sandbox's own process would follow. A copy that does not follow it is read.
        copy = f"/tmp/overmind-read-{uuid.uuid4().hex}"
        try:
            checked = sandbox.process.exec(
                _copy_command(f"{WORKSPACE}/conversations/{conversation}/{name}", copy),
                timeout=60,
            )
            if checked.exit_code != 0:
                raise FileNotFoundError(name)
            # And while it is read: the cell may still write it.
            received = bytearray()
            for chunk in sandbox.fs.download_file_stream(copy):
                received += chunk
                if len(received) > MAX_FILE_BYTES:
                    raise FileNotFoundError(name)
            return bytes(received)
        except DaytonaNotFoundError:
            raise FileNotFoundError(name) from None
        except DaytonaError as exc:
            raise WorkspaceUnavailableError(str(exc)) from exc
        finally:
            with contextlib.suppress(DaytonaError):
                sandbox.process.exec(f"rm -f {copy}", timeout=30)

    def forget(self, project: str, data: list[str], conversations: list[str]) -> None:
        command = _forget_command(data, conversations)
        # A stopped or archived workspace still holds the files: it starts to delete them.
        sandbox = self._sandbox(project, create=False) if command else None
        if sandbox is None:
            return
        for conversation in conversations:
            self.stop(project, conversation)
        try:
            deleted = sandbox.process.exec(command, timeout=60)
        except DaytonaError as exc:
            raise WorkspaceUnavailableError(str(exc)) from exc
        if deleted.exit_code != 0:
            raise WorkspaceUnavailableError(f"The files were not deleted: {deleted.result}")

    def remove(self, project: str) -> None:
        self._forget(project)
        try:
            for found in self.client.list(ListSandboxesQuery(labels=_labels(project))):
                with contextlib.suppress(DaytonaNotFoundError):
                    self.client.delete(self.client.get(found.id))
            for _ in range(START_SECONDS // 2):
                if not any(
                    True for _ in self.client.list(ListSandboxesQuery(labels=_labels(project)))
                ):
                    return
                time.sleep(2)
        except DaytonaError as exc:
            raise WorkspaceUnavailableError(str(exc)) from exc
        raise WorkspaceUnavailableError("The workspace is not deleted yet.")

    def stop(self, project: str, conversation: str) -> None:
        sandbox = self._sandbox(project, start=False)
        if sandbox is None:
            return
        work = f"{WORKSPACE}/conversations/{conversation}"
        with contextlib.suppress(DaytonaError):
            for context in sandbox.code_interpreter.list_contexts():
                if context.cwd == work:
                    self._end(project, conversation, sandbox, context)


def _forget_command(data: list[str], conversations: list[str]) -> str:
    paths = [f"{WORKSPACE}/data/{name}" for name in data if NAME.fullmatch(name)]
    paths += [
        f"{WORKSPACE}/conversations/{c}" for c in conversations if c.isalnum() and len(c) <= 64
    ]
    return "rm -rf " + " ".join(paths) if paths else ""


def _labels(project: str) -> dict[str, str]:
    return {"overmind": "workspace", "project": project}


def _state(sandbox) -> str:
    return str(getattr(sandbox.state, "value", sandbox.state))


def _reply(stdout: str) -> dict | None:
    """The runner's reply: the last line holding the marker followed by one JSON object.
    The cell's own output, inside that object, may hold the marker too."""
    decoder = json.JSONDecoder()
    for line in reversed(stdout.splitlines()):
        start = line.find(REPLY)
        while start >= 0:
            try:
                reply, _ = decoder.raw_decode(line, start + len(REPLY))
            except ValueError:
                start = line.find(REPLY, start + 1)
                continue
            if isinstance(reply, dict):
                return reply
            start = line.find(REPLY, start + 1)
    return None


def _copy_command(path: str, copy: str) -> str:
    """Copies a regular file with one link and at most MAX_FILE_BYTES, never a link's
    target: ``cp -P`` opens the source without following it."""
    return (
        f'[ ! -L {path} ] && [ -f {path} ] && [ "$(stat -c %h {path})" = 1 ] '
        f'&& [ "$(stat -c %s {path})" -le {MAX_FILE_BYTES} ] '
        f"&& cp -P {path} {copy} && [ ! -L {copy} ] && [ -f {copy} ]"
    )
