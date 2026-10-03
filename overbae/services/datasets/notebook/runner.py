"""Execute one cell body against a frame in a ``python3 -I`` child under cpu,
memory and file-size rlimits. Frames cross the boundary as Parquet. The limits
bound a runaway script, not an attacker."""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import tempfile
import time
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path

from overbae.services.datasets import examples, sampling, store
from overbae.services.datasets.notebook import libraries

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
CPU_SECONDS = 900
WALL_SECONDS = 1100
_ADDRESS_SPACE_BYTES = 6 * 1024**3
_FILE_SIZE_BYTES = 2 * 1024**3
_TAIL_CHARS = 1200
_INSPECT_TAIL_CHARS = 4000


@dataclass
class CellResult:
    path: Path | None
    error: str = ""
    stdout: str = ""
    ok: bool = False
    workspace: tempfile.TemporaryDirectory | None = field(default=None, repr=False)

    @property
    def frame(self):
        return store.read_frame(self.path) if self.path is not None else None


def audit(script: str, allowed: frozenset[str]) -> list[str]:
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


_LIMITS = f"""\
import resource
# No RLIMIT_NPROC: it counts every process of the uid, so any useful
# cap stops numpy from starting its thread pool.
for _limit, _ceiling in (
    (resource.RLIMIT_CPU, {CPU_SECONDS}),
    (resource.RLIMIT_AS, {_ADDRESS_SPACE_BYTES}),
    (resource.RLIMIT_FSIZE, {_FILE_SIZE_BYTES}),
):
    try:
        resource.setrlimit(_limit, (_ceiling, _ceiling))
    except (ValueError, OSError):
        pass
"""


def run(
    script: str, source: Path, *, library_cache: Path, produce_frame: bool = True, cancelled=None
) -> CellResult:
    mode = "cell" if produce_frame else "inspect"
    noun = "cell" if produce_frame else "script"
    stdout_tail = _TAIL_CHARS if produce_frame else _INSPECT_TAIL_CHARS
    violations = audit(script, libraries.allowed_imports(library_cache))
    if violations:
        return CellResult(None, error="; ".join(violations[:5]))
    workspace = tempfile.TemporaryDirectory(prefix="cell_")
    tmp = workspace.name
    tmp_path = Path(tmp)
    script_path = tmp_path / "cell.py"
    runner_path = tmp_path / "_runner.py"
    out_path = tmp_path / "out.parquet"
    script_path.write_text(script, encoding="utf-8")
    runtime = Path(__file__).with_name("cell_runtime.py")
    runner_path.write_text(
        _LIMITS + f"\nimport runpy\nrunpy.run_path({str(runtime)!r}, run_name='__main__')\n",
        encoding="utf-8",
    )
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "OMP_NUM_THREADS": "2",
        "HOME": tmp,
        "TIKTOKEN_CACHE_DIR": str(tmp_path / "tiktoken"),
    }
    try:
        with (tmp_path / "stdout").open("w") as stdout, (tmp_path / "stderr").open("w") as stderr:
            proc = subprocess.Popen(  # noqa: S603 — audited script, jailed child
                [
                    sys.executable,
                    "-I",
                    str(runner_path),
                    str(script_path),
                    str(source),
                    str(out_path),
                    mode,
                    str(library_cache) if library_cache.exists() else "",
                    str(Path(examples.__file__).resolve()),
                    str(Path(store.__file__).resolve()),
                    str(Path(sampling.__file__).resolve()),
                ],
                cwd=tmp,
                env=env,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
            deadline = time.monotonic() + WALL_SECONDS
            try:
                while proc.poll() is None:
                    if cancelled and cancelled():
                        proc.kill()
                        proc.wait()
                        return CellResult(None, error="Cancellation requested.")
                    if time.monotonic() >= deadline:
                        raise subprocess.TimeoutExpired(proc.args, WALL_SECONDS)
                    with suppress(subprocess.TimeoutExpired):
                        proc.wait(timeout=0.5)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
    except subprocess.TimeoutExpired:
        return CellResult(
            None, error=f"The {noun} ran longer than {WALL_SECONDS}s and was stopped."
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CellResult(None, error=f"The runner could not start: {exc}")
    with (tmp_path / "stdout").open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - stdout_tail))
        output = stream.read().decode(errors="replace")
        if size > stdout_tail:
            output = f"[stdout truncated; showing last {stdout_tail} of {size} bytes]\n" + output
    with (tmp_path / "stderr").open("rb") as stream:
        stream.seek(max(0, stream.seek(0, 2) - _TAIL_CHARS))
        error = stream.read().decode(errors="replace")
    if proc.returncode != 0:
        failure = (error or output).strip()[-_TAIL_CHARS:]
        if proc.returncode in (-9, 137):
            failure = failure or f"The {noun} used more memory or CPU than allowed."
        return CellResult(
            None,
            error=failure or f"The {noun} exited with code {proc.returncode}.",
            stdout=output,
        )
    if not produce_frame:
        return CellResult(None, stdout=output, ok=True)
    try:
        store.read_manifest(out_path)
        store.row_count(out_path)
    except (OSError, ValueError) as exc:
        return CellResult(None, error=f"The cell's output could not be read: {exc}")
    return CellResult(out_path, stdout=output, ok=True, workspace=workspace)
