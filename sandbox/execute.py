from __future__ import annotations

import ast
import contextlib
import fcntl
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from sandbox import libraries, tables

GIB = 1024**3
SCRIPT_CPU_SECONDS = 120
SCRIPT_WALL_SECONDS = 300
EXTRACT_CPU_SECONDS = 1200
EXTRACT_WALL_SECONDS = 1260
FILE_SIZE_BYTES = 2 * GIB
PROCESSES = 256
STDOUT_CHARS = 4000
ERROR_CHARS = 1200
RESERVE_WAIT_SECONDS = 120
UIDS = range(20000, 21000)
# Measured peak of a script: 4-5.5x the frame's uncompressed Parquet bytes.
_FRAME_FACTOR = 5.5
_CHILD_BASE_BYTES = 256 * 1024**2
_GROWTH = 1.5
_BUDGET_SHARE = 0.6
_WATCH_SECONDS = 0.2
_KILLED = (-signal.SIGKILL, -signal.SIGXCPU, 128 + signal.SIGKILL)

_FORBIDDEN_CALLS = frozenset(
    {"open", "exec", "eval", "compile", "__import__", "input", "breakpoint", "exit", "quit"}
)
_FORBIDDEN_ATTRS = frozenset(
    {
        "__globals__",
        "__builtins__",
        "__subclasses__",
        "__import__",
        "__loader__",
        "__spec__",
        "system",
        "popen",
        "spawn",
        "fork",
    }
)


class BusyError(Exception):
    pass


@dataclass
class Outcome:
    ok: bool = False
    error: str = ""
    stdout: str = ""
    seconds: float = 0.0
    output: Path | None = None


def gib(value: float) -> str:
    return f"{value / GIB:.1f}"


def _limit_from(path: str) -> int | None:
    try:
        raw = Path(path).read_text(encoding="ascii").strip()
    except OSError:
        return None
    return int(raw) if raw.isdigit() else None


def host_memory() -> int:
    found = [
        limit
        for limit in (
            _limit_from("/sys/fs/cgroup/memory.max"),
            _limit_from("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
        )
        if limit
    ]
    found.append(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES"))
    return min(found)


def memory_budget() -> int:
    configured = int(os.environ.get("SANDBOX_MEMORY_BYTES") or 0)
    return configured or int(host_memory() * _BUDGET_SHARE)


def script_reservation(source: Path) -> int:
    loaded = tables.uncompressed_bytes(source)
    return max(GIB, int(loaded * _FRAME_FACTOR) + _CHILD_BASE_BYTES)


def extract_reservation(document: Path) -> int:
    return max(2 * GIB, 4 * document.stat().st_size)


def audit(script: str) -> list[str]:
    allowed = libraries.allowed_imports()
    violations: list[str] = []
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        return [f"syntax error on line {exc.lineno}: {exc.msg}"]
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in allowed:
                    violations.append(f"import {alias.name} is not allowed")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".", 1)[0] not in allowed:
                violations.append(f"import from {node.module or '?'} is not allowed")
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
            if name in _FORBIDDEN_CALLS:
                violations.append(f"{name}() is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            violations.append(f"{node.attr} is not allowed")
    return sorted(set(violations))


def shared_uid() -> bool:
    return os.geteuid() != 0


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def _ledger() -> Iterator[dict]:
    path = Path(tempfile.gettempdir()) / "sandbox-ledger.json"
    with open(path, "a+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            try:
                entries = json.loads(fh.read() or "{}")
            except ValueError:
                entries = {}
            entries = {k: v for k, v in entries.items() if _alive(int(v["pid"]))}
            yield entries
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(entries))
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@contextlib.contextmanager
def reserved(need: int, budget: int) -> Iterator[int | None]:
    token = uuid.uuid4().hex
    deadline = time.monotonic() + RESERVE_WAIT_SECONDS
    while True:
        with _ledger() as entries:
            used = {e.get("uid") for e in entries.values()}
            free = [None] if shared_uid() else [u for u in UIDS if u not in used][:1]
            if free and sum(int(e["bytes"]) for e in entries.values()) + need <= budget:
                uid = free[0]
                entries[token] = {"pid": os.getpid(), "bytes": need, "uid": uid}
                break
        if time.monotonic() > deadline:
            raise BusyError
        time.sleep(0.5)
    try:
        yield uid
    finally:
        with _ledger() as entries:
            entries.pop(token, None)


def _members(leader: int, uid: int | None) -> list[tuple[int, int]]:
    page = os.sysconf("SC_PAGE_SIZE")
    found = []
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            if uid is not None and os.stat(f"/proc/{name}").st_uid != uid:
                continue
            with open(f"/proc/{name}/stat", encoding="ascii", errors="replace") as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if uid is None and int(fields[2]) != leader:
            continue
        found.append((int(name), int(fields[21]) * page))
    return found


def _kill(leader: int, uid: int | None) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(leader, signal.SIGKILL)
    # A child that left the group with setsid() still runs as the run's uid.
    if uid is not None:
        for pid, _ in _members(leader, uid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)


def hand_over(scratch: Path, uid: int | None) -> None:
    if uid is None:
        return
    for path in [scratch, *scratch.iterdir()]:
        os.chown(path, uid, uid)


def _take_back(scratch: Path, uid: int | None) -> None:
    if uid is None:
        return
    owner = os.getuid()
    for root, dirs, files in os.walk(scratch):
        for name in [*dirs, *files]:
            os.chown(Path(root) / name, owner, owner, follow_symlinks=False)
    os.chown(scratch, owner, owner)
    # A reused uid must not find files an earlier run left in a shared directory.
    for shared in ("/tmp", "/var/tmp", "/dev/shm"):
        for root, dirs, files in os.walk(shared):
            for name in [*dirs, *files]:
                path = Path(root) / name
                try:
                    if path.lstat().st_uid != uid:
                        continue
                    if path.is_dir() and not path.is_symlink():
                        shutil.rmtree(path, ignore_errors=True)
                        dirs.remove(name)
                    else:
                        path.unlink()
                except (OSError, ValueError):
                    continue


@dataclass
class Child:
    argv: list[str]
    noun: str
    cpu_seconds: int
    wall_seconds: int


def spawn(child: Child, scratch: Path, uid: int | None, limit: int) -> Outcome:
    hand_over(scratch, uid)
    rlimits = [child.cpu_seconds, FILE_SIZE_BYTES, 0 if uid is None else PROCESSES]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "OMP_NUM_THREADS": "2",
        "MALLOC_ARENA_MAX": "2",
        "HOME": str(scratch),
        "TIKTOKEN_CACHE_DIR": "/opt/sandbox/tiktoken",
    }
    identity = {} if uid is None else {"user": uid, "group": uid, "extra_groups": []}
    started = time.monotonic()
    stopped = ""
    with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
        try:
            proc = subprocess.Popen(
                [sys.executable, "-I", *child.argv[:1], json.dumps(rlimits), *child.argv[1:]],
                cwd=scratch,
                env=env,
                stdout=out,
                stderr=err,
                umask=0o077,
                start_new_session=True,
                **identity,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return Outcome(error=f"The runner could not start: {exc}")
        deadline = started + child.wall_seconds
        try:
            while True:
                try:
                    proc.wait(timeout=_WATCH_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if time.monotonic() > deadline:
                    stopped = (
                        f"The {child.noun} ran longer than {child.wall_seconds}s and was stopped."
                    )
                elif sum(rss for _, rss in _members(proc.pid, uid)) > limit:
                    stopped = (
                        f"The {child.noun} used more than {gib(limit)} GiB of memory "
                        "and was stopped."
                    )
                if stopped:
                    break
        finally:
            _kill(proc.pid, uid)
            proc.wait()
            _take_back(scratch, uid)
        out.seek(0)
        err.seek(0)
        stdout = out.read().decode("utf-8", "replace")
        stderr = err.read().decode("utf-8", "replace")
    seconds = round(time.monotonic() - started, 3)
    tail = stdout[-STDOUT_CHARS:]
    if stopped:
        return Outcome(error=stopped, stdout=tail, seconds=seconds)
    if proc.returncode != 0:
        failure = (stderr or stdout).strip()[-ERROR_CHARS:]
        if proc.returncode in _KILLED:
            failure = failure or f"The {child.noun} used more memory or CPU than allowed."
        return Outcome(
            error=failure or f"The {child.noun} exited with code {proc.returncode}.",
            stdout=tail,
            seconds=seconds,
        )
    return Outcome(ok=True, stdout=tail, seconds=seconds)


def run_script(script: str, scratch: Path, *, produce_frame: bool) -> Outcome:
    noun = "cell" if produce_frame else "script"
    violations = audit(script)
    if violations:
        return Outcome(error="; ".join(violations[:5]))
    source = scratch / "input.parquet"
    need, budget = script_reservation(source), memory_budget()
    if need > budget:
        return Outcome(
            error=f"This version needs about {gib(need)} GiB of memory for a pandas {noun}; "
            f"this worker has {gib(budget)} GiB."
        )
    (scratch / "script.py").write_text(script, encoding="utf-8")
    output = scratch / "output.parquet"
    child = Child(
        argv=[
            str(Path(__file__).with_name("child.py")),
            "script.py",
            source.name,
            output.name,
            "cell" if produce_frame else "inspect",
        ],
        noun=noun,
        cpu_seconds=SCRIPT_CPU_SECONDS,
        wall_seconds=SCRIPT_WALL_SECONDS,
    )
    with reserved(need, budget) as uid:
        outcome = spawn(child, scratch, uid, int(need * _GROWTH))
    if outcome.ok and produce_frame:
        if not output.exists():
            return Outcome(error="The cell's output could not be read.", stdout=outcome.stdout)
        outcome.output = output
    return outcome
