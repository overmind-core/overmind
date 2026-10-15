from __future__ import annotations

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
import threading
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from sandbox import packages

GIB = 1024**3
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
_GROWTH = 1.5
_BUDGET_SHARE = 0.6
_WATCH_SECONDS = 0.2
_DISK_EVERY = 5
_KILLED = (-signal.SIGKILL, -signal.SIGXCPU)


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
    exit_code: int | None = None
    runtime: dict | None = None


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


def need_for(header: dict) -> int:
    if header["mode"] == "package":
        # Keep main's measured dataframe expansion allowance when admitting JSONL packages.
        return max(GIB, int(header["input_bytes"] * 5.5) + 256 * 1024**2 + header["package_bytes"])
    return max(2 * GIB, 4 * int(header["input_bytes"]))


def refusal(header: dict, need: int, budget: int) -> str:
    if header["mode"] == "package":
        memory = header.get("limits", {}).get("memory_mb")
        if memory and memory * 1024**2 < need:
            return f"The input needs about {gib(need)} GiB; increase memory_mb or use batch_rows."
        if need > budget:
            return f"The input needs about {gib(need)} GiB; this sandbox has {gib(budget)} GiB. Use batch_rows for independent rows."
    if need <= budget:
        return ""
    return (
        f"This document needs about {gib(need)} GiB of memory to extract; "
        f"this worker has {gib(budget)} GiB."
    )


def input_matches(header: dict, path: Path) -> bool:
    return path.stat().st_size == int(header["input_bytes"])


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
            fh.flush()
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
    for path in [scratch, *scratch.rglob("*")]:
        os.chown(path, uid, uid, follow_symlinks=False)


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
    scratch_bytes: int | None = None


def spawn(
    child: Child, scratch: Path, slot: Slot, stop: threading.Event | None = None, on_progress=None
) -> Outcome:
    uid = slot.uid
    hand_over(scratch, uid)
    rlimits = [
        child.cpu_seconds,
        min(FILE_SIZE_BYTES, child.scratch_bytes or FILE_SIZE_BYTES),
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
        progress_offset = 0
        peak_rss = peak_files = None
        try:
            while True:
                try:
                    proc.wait(timeout=_WATCH_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if stop is not None and stop.is_set():
                    stopped = f"The {child.noun} was cancelled."
                    break
                if on_progress is not None:
                    progress_offset = read_progress(scratch, progress_offset, on_progress)
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
                peak_rss, peak_files = max(peak_rss or 0, rss), max(peak_files or 0, files)
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
                elif child.scratch_bytes is not None and files > child.scratch_bytes:
                    stopped = f"The {child.noun} exceeded its scratch_mb limit."
                if stopped:
                    break
        finally:
            _stop(proc.pid, uid)
            proc.wait()
            _take_back(scratch, uid)
        if on_progress is not None:
            read_progress(scratch, progress_offset, on_progress)
        stdout = _tail(out, STDOUT_CHARS)
        stderr = _tail(err, 2 * ERROR_CHARS)
    seconds = round(time.monotonic() - started, 3)
    facts = {
        "memory_limit_bytes": slot.limit,
        "observed_peak_rss_bytes": peak_rss,
        "observed_peak_scratch_bytes": peak_files,
        "sample_interval_seconds": _WATCH_SECONDS,
    }
    if stopped:
        return Outcome(
            error=stopped, stdout=stdout, seconds=seconds, exit_code=proc.returncode, runtime=facts
        )
    if proc.returncode != 0:
        failure = (stderr or stdout).strip()[-ERROR_CHARS:]
        if proc.returncode in _KILLED:
            failure = failure or f"The {child.noun} used more memory or CPU than allowed."
        return Outcome(
            error=failure or f"The {child.noun} exited with code {proc.returncode}.",
            stdout=stdout,
            seconds=seconds,
            exit_code=proc.returncode,
            runtime=facts,
        )
    return Outcome(ok=True, stdout=stdout, seconds=seconds, exit_code=0, runtime=facts)


def run(
    header: dict,
    scratch: Path,
    source: Path,
    slot: Slot,
    stop: threading.Event | None,
    on_progress=None,
) -> Outcome:
    if header["mode"] == "package":
        limits = header.get("limits", {})
        if "memory_mb" in limits:
            slot.limit = min(slot.limit, limits["memory_mb"] * 1024**2)
        child = Child(
            argv=[str(Path(__file__).with_name("package_child.py"))],
            noun="package",
            cpu_seconds=limits.get("seconds", 300) * limits.get("cpus", 2),
            wall_seconds=limits.get("seconds", 300),
            scratch_bytes=limits.get("scratch_mb", 2048) * 1024**2,
        )
        outcome = spawn(child, scratch, slot, stop)
        if outcome.ok:
            outcome.output = claim(scratch / "output.jsonl", packages.MAX_OUTPUT_BYTES)
            if outcome.output is None:
                outcome.ok = False
                outcome.error = "The package did not produce a readable regular output.jsonl."
        return outcome
    child = Child(
        argv=[
            str(Path(__file__).with_name("documents") / "child.py"),
            source.name,
            "output.json",
            "extraction.json",
        ],
        noun="document extraction",
        cpu_seconds=EXTRACT_CPU_SECONDS,
        wall_seconds=EXTRACT_WALL_SECONDS,
        # Any process can read another's command line; only its own uid can read its environment.
        env={"SANDBOX_FILENAME": header["filename"]},
    )
    outcome = spawn(child, scratch, slot, stop, on_progress)
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
    outcome.output = claim(scratch / "output.json")
    if outcome.output is None or not isinstance(outcome.extraction, dict):
        return unreadable
    return outcome


def read_progress(scratch: Path, offset: int, callback) -> int:
    report = claim(scratch / "progress.jsonl", 4 * 1024**2)
    if report is None:
        return offset
    with report:
        report.seek(offset)
        for _ in range(100):
            line = report.readline(8193)
            if not line.endswith(b"\n") or len(line) > 8192:
                break
            offset = report.tell()
            try:
                value = json.loads(line)
            except ValueError:
                continue
            if isinstance(value, dict):
                callback(value)
    return offset
