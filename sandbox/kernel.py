"""A persistent Python namespace behind a Unix socket, and the limits untrusted code runs
under. Every session provider runs this one file, so it imports the standard library only.

    kernel.py serve <socket> [limits-json]     hold the namespace and answer requests
    kernel.py send <socket>                    pass one request from stdin, print its reply
"""

import ast
import contextlib
import ctypes
import io
import json
import linecache
import os
import resource
import signal
import socket
import struct
import sys
import time
import traceback

OUTPUT_CHARS = 60_000
REMEMBERED = 32

# Landlock file rights by ABI version: each version adds rights after the 13 of the first.
_FS_RIGHTS = {1: 13, 2: 14, 3: 15, 4: 15, 5: 16}
_EXECUTE, _WRITE_FILE, _READ_FILE, _READ_DIR = 1, 2, 4, 8
_ON_FILES = _EXECUTE | _WRITE_FILE | _READ_FILE | (1 << 14) | (1 << 15)
_READ = _EXECUTE | _READ_FILE | _READ_DIR
_SYSTEM = ("/usr", "/lib", "/lib64", "/bin", "/sbin", "/etc", "/opt")
_SOCKET_CALL = {"x86_64": (0xC000003E, 41), "aarch64": (0xC00000B7, 198)}


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def landlock_abi() -> int:
    """The Landlock version this host enforces; 0 when it has none. Without it the uid of a
    run is all that keeps it from the files and process list of the others."""
    if sys.platform != "linux":
        return 0
    return max(0, _libc().syscall(444, None, ctypes.c_size_t(0), 1))


def _landlock(libc, work: str, readable: tuple[str, ...]) -> None:
    """Limit this process and its children to the files named here, deny TCP and keep its
    signals and abstract sockets to itself, as far as this Linux supports."""
    abi = landlock_abi()
    if not abi:
        return
    files = (1 << _FS_RIGHTS[min(abi, 5)]) - 1
    attributes = struct.pack("QQQ", files, 3 if abi >= 4 else 0, 3 if abi >= 6 else 0)
    size = 24 if abi >= 6 else 16 if abi >= 4 else 8
    ruleset = libc.syscall(444, attributes, ctypes.c_size_t(size), 0)
    if ruleset < 0:
        raise OSError(ctypes.get_errno(), "landlock_create_ruleset")
    rights = dict.fromkeys((*_SYSTEM, sys.prefix, sys.base_prefix, *readable), _READ)
    # Its own entry only: the rest of /proc names every other run's processes.
    rights |= dict.fromkeys(("/proc/self", "/proc/cpuinfo", "/proc/meminfo"), _READ)
    # duckdb aborts when it cannot read its memory limit under the cgroup tree.
    rights |= dict.fromkeys(("/sys/devices/system/cpu", "/sys/fs/cgroup"), _READ)
    rights |= dict.fromkeys(("/dev/null", "/dev/zero", "/dev/random", "/dev/urandom"), 6)
    # multiprocessing keeps its semaphores in /dev/shm; a run's own uid guards its files.
    rights |= dict.fromkeys((work, "/dev/shm"), files)
    for path, allowed in rights.items():
        try:
            target = os.open(path, os.O_PATH | os.O_CLOEXEC)
        except OSError:
            continue
        if not os.path.isdir(path):
            allowed &= _ON_FILES
        rule = struct.pack("=Qi", allowed & files, target)
        if libc.syscall(445, ruleset, 1, rule, 0):
            raise OSError(ctypes.get_errno(), f"landlock_add_rule {path}")
        os.close(target)
    if libc.syscall(446, ruleset, 0):
        raise OSError(ctypes.get_errno(), "landlock_restrict_self")
    os.close(ruleset)


def _unix_sockets_only(libc) -> None:
    """A seccomp filter: ``socket()`` answers EACCES for every family but AF_UNIX, so a run
    reaches no other run, whatever network its container has."""
    architecture, socket_call = _SOCKET_CALL[os.uname().machine]
    kill, allow, refuse = 0x80000000, 0x7FFF0000, 0x00050000 | 13
    program = [
        (0x20, 0, 0, 4),  # load the architecture
        (0x15, 1, 0, architecture),
        (0x06, 0, 0, kill),  # another ABI numbers its calls differently
        (0x20, 0, 0, 0),  # load the call number
        (0x35, 0, 1, 0x40000000),  # the x32 numbering of the same architecture
        (0x06, 0, 0, kill),
        (0x15, 0, 3, socket_call),
        (0x20, 0, 0, 16),  # load the socket family
        (0x15, 1, 0, socket.AF_UNIX),
        (0x06, 0, 0, refuse),
        (0x06, 0, 0, allow),
    ]
    filters = b"".join(struct.pack("HBBI", *instruction) for instruction in program)
    held = ctypes.create_string_buffer(filters)
    filter_program = struct.pack("HP", len(program), ctypes.addressof(held))
    if libc.prctl(22, 2, filter_program, 0, 0):
        raise OSError(ctypes.get_errno(), "seccomp")


def confine(work: str, readable: tuple[str, ...] = ()) -> None:
    """Keep this process and every child to ``work``, the libraries and ``readable``, and
    to Unix sockets. Runs of different projects share one container; these limits need no
    privilege and cannot be taken off again. A sandbox on another system than Linux is a
    development sandbox with no isolation at all."""
    if sys.platform != "linux":
        return
    libc = _libc()
    if libc.prctl(38, 1, 0, 0, 0):  # no_new_privs: an unprivileged filter requires it
        raise OSError(ctypes.get_errno(), "no_new_privs")
    _landlock(libc, work, readable)
    _unix_sockets_only(libc)


class Interrupted(BaseException):
    """Not an Exception: a cell's own ``except Exception`` must not swallow its deadline."""


def _interrupt(_signal, _frame):
    raise Interrupted


class _Output(io.TextIOBase):
    """What a cell printed, kept to its first and last halves: a loop that prints without
    end cannot fill the kernel's memory."""

    half = OUTPUT_CHARS // 2

    def __init__(self):
        self.head, self.tail, self.omitted = "", "", 0

    def writable(self):
        return True

    def write(self, text):
        room = self.half - len(self.head)
        self.head += text[:room]
        if rest := text[room:]:
            kept = self.tail + rest
            self.omitted += max(0, len(kept) - self.half)
            self.tail = kept[-self.half :]
        return len(text)

    def result(self) -> tuple[str, bool]:
        gap = f"\n… {self.omitted:,} characters omitted …\n" if self.omitted else ""
        # A lone surrogate is valid in a Python string and in no JSON a server will send on.
        text = (self.head + gap + self.tail).encode("utf-8", "replace").decode()
        return text, bool(self.omitted)


def run(code, namespace, timeout, cell):
    printed, failure = _Output(), _Output()
    started = time.monotonic()
    # One name per cell: a traceback through an earlier cell's function shows that cell.
    linecache.cache[cell] = (len(code), None, code.splitlines(keepends=True), cell)
    signal.alarm(timeout)
    try:
        with contextlib.redirect_stdout(printed), contextlib.redirect_stderr(printed):
            tree = ast.parse(code, cell)
            shown = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
            exec(compile(tree, cell, "exec"), namespace)
            if shown is not None:
                value = eval(compile(ast.Expression(shown.value), cell, "eval"), namespace)
                if value is not None:
                    print(repr(value))
    except Interrupted:
        failure.write(f"The execution ran longer than {timeout}s and was interrupted.")
    except BaseException as exc:
        failure.write(
            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__.tb_next))
        )
    finally:
        signal.alarm(0)
    output, truncated = printed.result()
    error = failure.result()[0]
    return {
        "ok": not error,
        "output": output,
        "error": error,
        "truncated": truncated,
        "seconds": round(time.monotonic() - started, 3),
    }


def serve(path, limits):
    for name, value in limits.get("rlimits", {}).items():
        resource.setrlimit(getattr(resource, name), (value, value))
    if "cpus" in limits:
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[: limits["cpus"]])
    signal.signal(signal.SIGALRM, _interrupt)
    # A kernel has no display: plots are files.
    os.environ.setdefault("MPLBACKEND", "Agg")
    work = os.getcwd()
    sys.path.insert(0, work)
    # Before the socket exists: a kernel that answers is a confined kernel.
    confine(work, readable=(os.path.join(os.path.dirname(work), "inputs"),))
    namespace = {"__name__": "__main__"}
    answered = {}
    cells = 0
    listener = socket.socket(socket.AF_UNIX)
    listener.bind(path)
    listener.listen()
    while True:
        connection, _ = listener.accept()
        # A caller that left, or sent no request, takes nothing down with it.
        with (
            contextlib.suppress(OSError, ValueError, KeyError),
            connection,
            connection.makefile("rwb") as stream,
        ):
            request = json.loads(stream.readline())
            # A caller that lost a reply sends the same id again and must not run it twice.
            code, reply = answered.get(request["id"], (None, None))
            if reply is None:
                cells += 1
                reply = run(request["code"], namespace, request["timeout"], f"<cell {cells}>")
                answered[request["id"]] = (request["code"], reply)
                if len(answered) > REMEMBERED:
                    del answered[next(iter(answered))]
            elif code != request["code"]:
                reply = {
                    "ok": False,
                    "output": "",
                    "error": "This execution id already ran different code.",
                    "truncated": False,
                    "seconds": 0.0,
                }
            stream.write(json.dumps(reply).encode())


def send(path):
    with socket.socket(socket.AF_UNIX) as connection:
        connection.connect(path)
        connection.sendall(sys.stdin.buffer.read().strip() + b"\n")
        with connection.makefile("rb") as stream:
            sys.stdout.buffer.write(stream.read())


if __name__ == "__main__":
    if sys.argv[1] == "serve":
        serve(sys.argv[2], json.loads(sys.argv[3]) if len(sys.argv) > 3 else {})
    else:
        send(sys.argv[2])
