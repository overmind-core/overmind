from __future__ import annotations

import ast
import contextlib
import fcntl
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sandbox import libraries, tables

GIB = 1024**3
SCRIPT_CPU_SECONDS = 120
SCRIPT_WALL_SECONDS = 300
EXTRACT_CPU_SECONDS = 1200
EXTRACT_WALL_SECONDS = 1260
FILE_SIZE_BYTES = 2 * GIB
PROCESSES = 256
FILE_DESCRIPTORS = 1024
MAX_FILES = 50_000
STDOUT_CHARS = 4000
ERROR_CHARS = 1200
RESERVE_WAIT_SECONDS = 120
KILL_SECONDS = 10
# Longer than any run plus its reply; a reservation a lost request left behind expires.
RESERVATION_SECONDS = 2 * 60 * 60
EXTRACTION_BYTES = 1024**2
UIDS = range(20000, 21000)
SHARED_DIRS = ("/tmp", "/var/tmp", "/dev/shm")
# Measured peak of a script: 4-5.5x the frame's uncompressed Parquet bytes.
_FRAME_FACTOR = 5.5
_CHILD_BASE_BYTES = 256 * 1024**2
_GROWTH = 1.5
_BUDGET_SHARE = 0.6
_WATCH_SECONDS = 0.2
_DISK_EVERY = 5
_KILLED = (-signal.SIGKILL, -signal.SIGXCPU)

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
    output: BinaryIO | None = None
    extraction: dict | None = None


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
            # re.compile and df.eval are methods; only the builtins themselves are refused.
            if isinstance(node.func, ast.Name) and node.func.id in _FORBIDDEN_CALLS:
                violations.append(f"{node.func.id}() is not allowed")
        elif isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRS:
            violations.append(f"{node.attr} is not allowed")
    return sorted(set(violations))


SCRIPT_MODES = ("cell", "inspect")


def need_for(header: dict) -> int:
    """``input_bytes`` is the frame's uncompressed size for a script and the file's size
    for a document."""
    declared = int(header["input_bytes"])
    if header["mode"] == "extract":
        return max(2 * GIB, 4 * declared)
    return max(GIB, int(declared * _FRAME_FACTOR) + _CHILD_BASE_BYTES)


def refusal(header: dict, need: int, budget: int) -> str:
    if header["mode"] in SCRIPT_MODES:
        violations = audit(header["script"])
        if violations:
            return "; ".join(violations[:5])
    if need <= budget:
        return ""
    if header["mode"] == "extract":
        return (
            f"This document needs about {gib(need)} GiB of memory to extract; "
            f"this worker has {gib(budget)} GiB."
        )
    noun = "cell" if header["mode"] == "cell" else "script"
    return (
        f"This version needs about {gib(need)} GiB of memory for a pandas {noun}; "
        f"this worker has {gib(budget)} GiB."
    )


def input_matches(header: dict, path: Path) -> bool:
    declared = int(header["input_bytes"])
    if header["mode"] == "extract":
        return path.stat().st_size == declared
    try:
        return tables.uncompressed_bytes(path) <= declared
    except (OSError, ValueError):
        return False


def can_trace() -> bool:
    """Root reads another uid's /proc/<pid>/fd only with CAP_SYS_PTRACE."""
    for line in Path("/proc/self/status").read_text(encoding="ascii").splitlines():
        if line.startswith("CapEff:"):
            return bool(int(line.split()[1], 16) & (1 << 19))
    return False


def shared_uid() -> bool:
    return os.geteuid() != 0


def runs_dir() -> Path:
    # A run owns its scratch dir, and a sticky /tmp lets an owner rename its dir and leave
    # a symlink in its place; under a parent only the server can write, it cannot.
    path = Path(tempfile.gettempdir()) / "sandbox"
    with contextlib.suppress(FileExistsError):
        path.mkdir(mode=0o711)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise RuntimeError(f"{path} is not a directory this server owns.")
    if stat.S_IMODE(info.st_mode) != 0o711:
        path.chmod(0o711)
    return path


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
    # flock needs no write access: a run that could open the ledger could hold its lock.
    fd = os.open(
        runs_dir() / "ledger.json", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600
    )
    os.fchmod(fd, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            fh.seek(0)
            try:
                saved = json.loads(fh.read() or "{}")
            except ValueError:
                saved = {}
            now = time.time()
            ledger = {
                "runs": {
                    k: v
                    for k, v in saved.get("runs", {}).items()
                    if _alive(int(v["pid"]))
                    and now - float(v.get("since", now)) < RESERVATION_SECONDS
                },
                "released": saved.get("released", {}),
            }
            yield ledger
            fh.seek(0)
            fh.truncate()
            fh.write(json.dumps(ledger))
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def _processes() -> Iterator[tuple[int, int, int, int]]:
    """``(pid, uid, process group, rss bytes)`` of each live process. A zombie holds no
    memory and cannot be killed again."""
    page = os.sysconf("SC_PAGE_SIZE")
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            uid = os.stat(f"/proc/{name}").st_uid
            with open(f"/proc/{name}/stat", encoding="ascii", errors="replace") as fh:
                fields = fh.read().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        if fields[0] in ("Z", "X"):
            continue
        yield int(name), uid, int(fields[2]), int(fields[21]) * page


def _members(leader: int, uid: int | None) -> list[tuple[int, int]]:
    if uid is None:
        return [(pid, rss) for pid, _, group, rss in _processes() if group == leader]
    return [(pid, rss) for pid, owner, _, rss in _processes() if owner == uid]


def _kill_all(uid: int) -> None:
    for pid, _ in _members(0, uid):
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, signal.SIGKILL)


def _stop(leader: int, uid: int | None) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(leader, signal.SIGKILL)
    if uid is None:
        return
    # A process forked after a pass scanned /proc survives it, and a child that left
    # the group with setsid() still runs as the run's uid: repeat until none is left.
    deadline = time.monotonic() + KILL_SECONDS
    while _members(leader, uid) and time.monotonic() < deadline:
        _kill_all(uid)
        time.sleep(0.05)


def _free_uid(ledger: dict) -> int | None:
    taken = {entry["uid"] for entry in ledger["runs"].values()}
    lingering = {owner for _, owner, _, _ in _processes() if owner in UIDS}
    for uid in lingering - taken:
        _kill_all(uid)
    free = [uid for uid in UIDS if uid not in taken and uid not in lingering]
    if not free:
        return None
    released = ledger["released"]
    return min(free, key=lambda uid: released.get(str(uid), 0.0))


@dataclass
class Slot:
    token: str
    uid: int | None
    limit: int


def reserve(need: int, budget: int) -> Slot:
    token = uuid.uuid4().hex
    deadline = time.monotonic() + RESERVE_WAIT_SECONDS
    while True:
        with _ledger() as ledger:
            runs = ledger["runs"]
            if sum(int(entry["bytes"]) for entry in runs.values()) + need <= budget:
                uid = None if shared_uid() else _free_uid(ledger)
                if shared_uid() or uid is not None:
                    runs[token] = {
                        "pid": os.getpid(),
                        "bytes": need,
                        "uid": uid,
                        "since": time.time(),
                    }
                    return Slot(token, uid, int(need * _GROWTH))
        if time.monotonic() > deadline:
            raise BusyError
        time.sleep(0.5)


def release(slot: Slot) -> None:
    with _ledger() as ledger:
        ledger["runs"].pop(slot.token, None)
        if slot.uid is not None:
            ledger["released"][str(slot.uid)] = time.time()


class TooManyFilesError(Exception):
    pass


def _allocated(root: str | Path, uid: int | None, seen: list[int]) -> int:
    skip = str(runs_dir())
    total = 0
    for parent, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if os.path.join(parent, d) != skip]
        for name in [*dirs, *files]:
            try:
                info = os.lstat(os.path.join(parent, name))
            except OSError:
                continue
            if uid is not None and info.st_uid != uid:
                continue
            total += info.st_blocks * 512
            seen[0] += 1
            # Empty files use inodes, which cost kernel memory, and slow every walk.
            if seen[0] > MAX_FILES:
                raise TooManyFilesError
    return total


def _unlinked_bytes(pids: list[int]) -> int:
    """An open file with no name (a memfd, or a file unlinked while open) is memory no
    directory walk finds."""
    total = 0
    counted: set[tuple[int, int]] = set()
    for pid in pids:
        try:
            fds = os.listdir(f"/proc/{pid}/fd")
        except OSError:
            continue
        for fd in fds:
            try:
                info = os.stat(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            key = (info.st_dev, info.st_ino)
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 0 and key not in counted:
                counted.add(key)
                total += info.st_blocks * 512
    return total


def _files_bytes(scratch: Path, uid: int | None, pids: list[int], held: list[BinaryIO]) -> int:
    # /tmp is a tmpfs: what a run writes there is memory the RSS sum does not see.
    seen = [0]
    total = _allocated(scratch, None, seen)
    if uid is not None:
        total += sum(_allocated(shared, uid, seen) for shared in SHARED_DIRS)
    total += sum(os.fstat(fh.fileno()).st_blocks * 512 for fh in held)
    return total + _unlinked_bytes(pids)


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
    skip = str(runs_dir())
    for shared in SHARED_DIRS:
        for root, dirs, files in os.walk(shared):
            dirs[:] = [d for d in dirs if os.path.join(root, d) != skip]
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


def claim(path: Path, max_bytes: int | None = None) -> BinaryIO | None:
    """Open a file the run wrote. The run controls the name: it can be a symlink to
    another run's data, a FIFO that blocks ``open`` for ever, or a hard link."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    info = os.fstat(fd)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or (max_bytes is not None and info.st_size > max_bytes)
    ):
        os.close(fd)
        return None
    return os.fdopen(fd, "rb")


def _tail(fh: BinaryIO, chars: int) -> str:
    size = fh.seek(0, os.SEEK_END)
    fh.seek(max(0, size - 4 * chars))
    return fh.read().decode("utf-8", "replace")[-chars:]


@dataclass
class Child:
    argv: list[str]
    noun: str
    cpu_seconds: int
    wall_seconds: int
    env: dict[str, str] | None = None


def spawn(child: Child, scratch: Path, slot: Slot) -> Outcome:
    uid = slot.uid
    hand_over(scratch, uid)
    rlimits = [
        child.cpu_seconds,
        FILE_SIZE_BYTES,
        0 if uid is None else PROCESSES,
        FILE_DESCRIPTORS,
    ]
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "OMP_NUM_THREADS": "2",
        "MALLOC_ARENA_MAX": "2",
        "HOME": str(scratch),
        "TMPDIR": str(scratch),
        "TIKTOKEN_CACHE_DIR": "/opt/sandbox/tiktoken",
        **(child.env or {}),
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
        files = 0
        tick = 0
        try:
            while True:
                try:
                    proc.wait(timeout=_WATCH_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass
                members = _members(proc.pid, uid)
                if tick % _DISK_EVERY == 0:
                    try:
                        files = _files_bytes(scratch, uid, [pid for pid, _ in members], [out, err])
                    except TooManyFilesError:
                        stopped = (
                            f"The {child.noun} created more than {MAX_FILES:,} files "
                            "and was stopped."
                        )
                        break
                tick += 1
                rss = sum(rss for _, rss in members)
                if time.monotonic() > deadline:
                    stopped = (
                        f"The {child.noun} ran longer than {child.wall_seconds}s and was stopped."
                    )
                elif rss > slot.limit:
                    stopped = (
                        f"The {child.noun} used more than {gib(slot.limit)} GiB of memory "
                        "and was stopped."
                    )
                elif rss + files > slot.limit:
                    stopped = (
                        f"The {child.noun} used more than {gib(slot.limit)} GiB of memory and "
                        "scratch files and was stopped."
                    )
                if stopped:
                    break
        finally:
            _stop(proc.pid, uid)
            proc.wait()
            _take_back(scratch, uid)
        stdout = _tail(out, STDOUT_CHARS)
        stderr = _tail(err, 2 * ERROR_CHARS)
    seconds = round(time.monotonic() - started, 3)
    if stopped:
        return Outcome(error=stopped, stdout=stdout, seconds=seconds)
    if proc.returncode != 0:
        failure = (stderr or stdout).strip()[-ERROR_CHARS:]
        if proc.returncode in _KILLED:
            failure = failure or f"The {child.noun} used more memory or CPU than allowed."
        return Outcome(
            error=failure or f"The {child.noun} exited with code {proc.returncode}.",
            stdout=stdout,
            seconds=seconds,
        )
    return Outcome(ok=True, stdout=stdout, seconds=seconds)


def run(header: dict, scratch: Path, source: Path, slot: Slot) -> Outcome:
    if header["mode"] == "extract":
        return _extract(header, scratch, source, slot)
    produce_frame = header["mode"] == "cell"
    noun = "cell" if produce_frame else "script"
    (scratch / "script.py").write_text(header["script"], encoding="utf-8")
    child = Child(
        argv=[
            str(Path(__file__).with_name("child.py")),
            "script.py",
            source.name,
            "output.parquet",
            "cell" if produce_frame else "inspect",
        ],
        noun=noun,
        cpu_seconds=SCRIPT_CPU_SECONDS,
        wall_seconds=SCRIPT_WALL_SECONDS,
    )
    outcome = spawn(child, scratch, slot)
    if outcome.ok and produce_frame:
        outcome.output = claim(scratch / "output.parquet")
        if outcome.output is None:
            return Outcome(error="The cell's output could not be read.", stdout=outcome.stdout)
    return outcome


def _extract(header: dict, scratch: Path, source: Path, slot: Slot) -> Outcome:
    child = Child(
        argv=[
            str(Path(__file__).with_name("documents") / "child.py"),
            source.name,
            "output.parquet",
            "extraction.json",
        ],
        noun="document extraction",
        cpu_seconds=EXTRACT_CPU_SECONDS,
        wall_seconds=EXTRACT_WALL_SECONDS,
        # Any process can read another's command line; only its own uid can read its environment.
        env={"SANDBOX_FILENAME": header["filename"]},
    )
    outcome = spawn(child, scratch, slot)
    if not outcome.ok:
        return outcome
    unreadable = Outcome(error="The extraction's output could not be read.", stdout=outcome.stdout)
    report = claim(scratch / "extraction.json", EXTRACTION_BYTES)
    if report is None:
        return unreadable
    with report:
        try:
            outcome.extraction = json.loads(report.read())
        except ValueError:
            return unreadable
    outcome.output = claim(scratch / "output.parquet")
    if outcome.output is None or not isinstance(outcome.extraction, dict):
        return unreadable
    return outcome
