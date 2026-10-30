"""Refuse new test seams on our own code.

A test may capture a Celery dispatch (``.delay`` / ``.apply_async``) and fake
outside services through ``tests/fakes``. It may not patch other ``overbae.*``
targets or import private ``overbae`` names, and a journey may not wait on wall
time, animations or fonts. A Console test may mock third-party modules and the
network edge (``@/client``, ``@/openapi``), not our other modules. Existing seams are counted, not listed: a change fails
only when it raises a count across all test files, so moving a test passes.
"""

from __future__ import annotations

import ast
import os
import re
import subprocess
import sys
from collections import Counter
from itertools import chain
from pathlib import Path

PATCH_TARGET = re.compile(r"""(?:patch|setattr)\(\s*["'](overbae\.[\w.]+)["']""")
CELERY_CAPTURES = (".delay", ".apply_async")
JOURNEY_WAIT = re.compile(r"time\.sleep\(|getAnimations|document\.fonts|fontFamily")
VITEST_MOCK = re.compile(r"""vi\.mock\(\s*["']((?:@/|\.{1,2}/)[^"']+)["']""")
NETWORK_EDGE = ("@/client", "@/openapi")
CONSOLE_TESTS = (".test.ts", ".test.tsx")


def _git(*args: str, stdin: bytes | None = None) -> bytes:
    return subprocess.run(["git", *args], input=stdin, capture_output=True, check=True).stdout


def _tracked(path: str) -> bool:
    if path.startswith("frontend/src/"):
        return path.endswith(CONSOLE_TESTS)
    return path.startswith("tests/") and path.endswith(".py")


def _committed(ref: str) -> dict[str, str]:
    listed = _git("ls-tree", "-r", "-z", "--name-only", ref, "--", "tests", "frontend/src")
    paths = [path for path in listed.decode().split("\0") if _tracked(path)]
    blobs = _git("cat-file", "--batch", stdin="".join(f"{ref}:{p}\n" for p in paths).encode())
    sources, offset = {}, 0
    for path in paths:
        header = blobs.index(b"\n", offset)
        size = int(blobs[offset:header].split()[2])
        sources[path] = blobs[header + 1 : header + 1 + size].decode("utf-8", errors="replace")
        offset = header + 2 + size
    return sources


def _working() -> dict[str, str]:
    listed = _git(
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
        "--",
        "tests",
        "frontend/src",
    )
    return {
        path: Path(path).read_text(encoding="utf-8")
        for path in listed.decode().split("\0")
        if _tracked(path) and Path(path).is_file()
    }


def _patches(source: str) -> list[str]:
    return [t for t in PATCH_TARGET.findall(source) if not t.endswith(CELERY_CAPTURES)]


def _private_imports(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [
        f"{node.module}.{alias.name}"
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("overbae")
        for alias in node.names
        if alias.name.startswith("_")
    ]


def _journey_waits(source: str) -> list[str]:
    return JOURNEY_WAIT.findall(source)


def _console_mocks(source: str) -> list[str]:
    return [t for t in VITEST_MOCK.findall(source) if not t.startswith(NETWORK_EDGE)]


KINDS = (
    ("patch of", _patches, "tests/"),
    ("private import of", _private_imports, "tests/"),
    ("wait on", _journey_waits, "tests/journeys/"),
    ("mock of", _console_mocks, "frontend/src/"),
)


def main() -> int:
    before = _committed(os.environ.get("PRE_COMMIT_FROM_REF") or "HEAD")
    after = _working()
    failures = []
    for kind, count, scope in KINDS:
        old = {path: count(source) for path, source in before.items() if path.startswith(scope)}
        new = {path: count(source) for path, source in after.items() if path.startswith(scope)}
        added = Counter(chain.from_iterable(new.values())) - Counter(
            chain.from_iterable(old.values())
        )
        for target in sorted(added):
            paths = [
                path
                for path, found in sorted(new.items())
                if found.count(target) > old.get(path, []).count(target)
            ]
            failures.append(f"new {kind} {target}: {', '.join(paths)}")
    for failure in failures:
        print(failure)
    if failures:
        print(
            "Fake outside services through tests/fakes, call public entry points and wait "
            "on observable state; see the run-tests skill."
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
