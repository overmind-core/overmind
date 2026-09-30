"""Refuse new test seams on our own code.

A test may capture a Celery dispatch (``.delay`` / ``.apply_async``) and fake
outside services through ``tests/fakes``. It may not patch other ``overbae.*``
targets or import private ``overbae`` names. Existing seams are counted, not
listed: a file fails only when a change adds one.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys

PATCH_TARGET = re.compile(r"""(?:patch|setattr)\(\s*["'](overbae\.[\w.]+)["']""")
CELERY_CAPTURES = (".delay", ".apply_async")


def _source(ref: str, path: str) -> str:
    result = subprocess.run(["git", "show", f"{ref}:{path}"], capture_output=True, text=True)
    return result.stdout if result.returncode == 0 else ""


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


def main(paths: list[str]) -> int:
    failures = []
    for path in paths:
        before, after = _source("HEAD", path), _source("", path)
        for kind, count in (("patch of", _patches), ("private import of", _private_imports)):
            added = sorted(set(count(after)) - set(count(before)))
            if len(count(after)) > len(count(before)):
                failures.append(f"{path}: new {kind} {', '.join(added) or 'our own code'}")
    for failure in failures:
        print(failure)
    if failures:
        print(
            "Fake outside services through tests/fakes and call public entry points; "
            "see the run-tests skill."
        )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
