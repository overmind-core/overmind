import ctypes
import os
import resource
import runpy
import sys
from pathlib import Path


def restrict(directory):
    libc = ctypes.CDLL(None, use_errno=True)
    abi = libc.syscall(444, 0, 0, 1)
    if abi < 1:
        raise RuntimeError("Clean replay requires Linux Landlock filesystem isolation")
    handled = (1 << (16 if abi >= 5 else 15 if abi >= 3 else 14 if abi >= 2 else 13)) - 1
    ruleset = ctypes.c_uint64(handled)
    descriptor = libc.syscall(444, ctypes.byref(ruleset), ctypes.sizeof(ruleset), 0)
    if descriptor < 0:
        raise OSError(ctypes.get_errno(), "Cannot create replay filesystem rules")

    class Rule(ctypes.Structure):
        _pack_ = 1
        _fields_ = [("access", ctypes.c_uint64), ("parent", ctypes.c_int)]

    try:
        readable = (1 << 0) | (1 << 2) | (1 << 3)
        for path, access in [
            (directory, handled),
            *[
                (Path(p), readable)
                for p in (
                    "/usr",
                    "/lib",
                    "/lib64",
                    "/opt/overmind-runtime",
                    "/etc/ld.so.cache",
                    "/etc/localtime",
                    "/dev/urandom",
                    "/dev/null",
                )
            ],
        ]:
            if not path.exists():
                continue
            fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                rule = Rule(access if path.is_dir() else access & ~8, fd)
                if libc.syscall(445, descriptor, 1, ctypes.byref(rule), 0) != 0:
                    raise OSError(ctypes.get_errno(), "Cannot install replay filesystem rule")
            finally:
                os.close(fd)
        if libc.prctl(38, 1, 0, 0, 0) != 0 or libc.syscall(446, descriptor, 0) != 0:
            raise OSError(ctypes.get_errno(), "Cannot enforce replay filesystem isolation")
    finally:
        os.close(descriptor)


def main():
    directory = Path(sys.argv[1]).resolve()
    seconds = int(sys.argv[2])
    entrypoint = sys.argv[3]
    sys.argv = sys.argv[3:]
    if os.geteuid() != 0:
        raise RuntimeError("Clean replay requires the trusted workspace supervisor")
    os.setgroups([])
    os.setgid(10002)
    os.setuid(10002)
    os.chdir(directory)
    resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds))
    restrict(directory)
    sys.path.insert(0, str(directory))
    runpy.run_path(entrypoint, run_name="__main__")


if __name__ == "__main__":
    main()
