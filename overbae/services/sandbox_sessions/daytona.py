import base64
import contextlib
import hashlib
import json
import shutil
import tempfile
import tomllib
from functools import cache
from typing import BinaryIO

from daytona import (
    CreateSandboxFromSnapshotParams,
    CreateSnapshotParams,
    Daytona,
    DaytonaBadRequestError,
    DaytonaConfig,
    DaytonaConflictError,
    DaytonaError,
    DaytonaNotFoundError,
    DaytonaTimeoutError,
    Image,
    Resources,
    Sandbox,
)
from django.conf import settings

from overbae.services.sandbox_sessions import (
    Execution,
    SessionGoneError,
    SessionUnavailableError,
)
from sandbox.sessions import IDLE_SECONDS, KERNEL, MAX_OUTPUT_BYTES, NAME

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
PYTHON = "3.13"
TOKENIZER_ENCODINGS = ("cl100k_base", "o200k_base")
KERNEL_PATH = "/opt/overmind/kernel.py"
TOKENIZERS = "/opt/overmind/tiktoken"
RESOURCES = Resources(cpu=2, memory=4, disk=5)
START_SECONDS = 120
REPLY_GRACE_SECONDS = 30
START_KERNEL = (
    "mkdir -p session/inputs session/work && cd session/work && "
    f"(nohup python {KERNEL_PATH} serve kernel.sock > /dev/null 2>&1 &) && "
    "for attempt in $(seq 200); do [ -S kernel.sock ] && sleep 0.1 && exit 0; sleep 0.05; done; "
    "exit 1"
)


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
    return f"overmind-session-{digest.hexdigest()[:12]}"


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


class DaytonaProvider:
    """Sessions in Daytona sandboxes: one sandbox per session, deleted when it stops."""

    def __init__(self):
        self.client = _client()

    def _find(self, session: str) -> Sandbox:
        try:
            return self.client.get(session)
        except (DaytonaNotFoundError, DaytonaBadRequestError):
            # Not found, or not a Daytona id: a session another provider started.
            raise SessionGoneError("The session does not exist.") from None
        except DaytonaError as exc:
            raise SessionUnavailableError(str(exc)) from exc

    def _lost(self, sandbox: Sandbox, exc: DaytonaError):
        """A failed call on a sandbox that no longer runs means the session is gone."""
        try:
            sandbox.refresh_data()
        except DaytonaNotFoundError:
            return SessionGoneError("The session was closed.")
        except DaytonaError:
            return SessionUnavailableError(str(exc))
        if sandbox.state != "started":
            return SessionGoneError(f"The session was closed (sandbox {sandbox.state}).")
        return SessionUnavailableError(str(exc))

    def _close(self, sandbox: Sandbox, reason: str) -> SessionGoneError:
        with contextlib.suppress(DaytonaError):
            self.client.delete(sandbox)
        return SessionGoneError(reason)

    def start(self) -> str:
        try:
            sandbox = self.client.create(
                CreateSandboxFromSnapshotParams(
                    snapshot=snapshot_name(),
                    labels={"overmind": "session"},
                    network_block_all=True,
                    public=False,
                    ephemeral=True,
                    auto_stop_interval=IDLE_SECONDS // 60,
                ),
                timeout=START_SECONDS,
            )
        except DaytonaError as exc:
            raise SessionUnavailableError(str(exc)) from exc
        try:
            started = sandbox.process.exec(START_KERNEL, timeout=60).exit_code == 0
        except DaytonaError:
            started = False
        if not started:
            self._close(sandbox, "")
            raise SessionUnavailableError("The kernel did not start.")
        return sandbox.id

    def execute(self, session: str, code: str, *, execution_id: str, timeout: int) -> Execution:
        sandbox = self._find(session)
        request = json.dumps({"id": execution_id, "code": code, "timeout": timeout}).encode()
        command = (
            f"echo {base64.b64encode(request).decode()} | base64 -d | "
            f"python {KERNEL_PATH} send session/work/kernel.sock"
        )
        stuck = "The kernel stopped answering and the session was closed."
        try:
            reply = sandbox.process.exec(command, timeout=timeout + REPLY_GRACE_SECONDS)
        except DaytonaTimeoutError:
            # The kernel's own deadline did not fire: it is stuck outside Python.
            raise self._close(sandbox, stuck) from None
        except DaytonaError as exc:
            raise self._lost(sandbox, exc) from exc
        try:
            return Execution(**json.loads(reply.result))
        except (ValueError, TypeError):
            raise self._close(sandbox, stuck) from None

    def put_file(self, session: str, name: str, source: BinaryIO) -> None:
        if not NAME.fullmatch(name):
            raise ValueError("Invalid file name.")
        sandbox = self._find(session)
        path = f"session/inputs/{name}"
        with tempfile.NamedTemporaryFile() as local:
            shutil.copyfileobj(source, local)
            local.flush()
            try:
                sandbox.fs.upload_file(local.name, path)
                # Against an accident only: the kernel's confinement is what holds.
                sandbox.fs.set_file_permissions(path, mode="444")
            except DaytonaError as exc:
                raise self._lost(sandbox, exc) from exc

    def get_file(self, session: str, name: str) -> bytes:
        if not NAME.fullmatch(name):
            raise FileNotFoundError(name)
        sandbox = self._find(session)
        path = f"session/work/{name}"
        try:
            # Its size first: the sandbox's disk holds more than a worker's memory.
            if sandbox.fs.get_file_info(path).size > MAX_OUTPUT_BYTES:
                raise FileNotFoundError(name)
            return sandbox.fs.download_file(path)
        except DaytonaNotFoundError:
            raise FileNotFoundError(name) from None
        except DaytonaError as exc:
            raise self._lost(sandbox, exc) from exc

    def stop(self, session: str) -> None:
        try:
            self.client.delete(self._find(session))
        except (SessionGoneError, DaytonaNotFoundError, DaytonaConflictError):
            # A conflict here means the sandbox is already being removed.
            return
        except DaytonaError as exc:
            raise SessionUnavailableError(str(exc)) from exc
