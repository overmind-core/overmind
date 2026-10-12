"""Execute one cell body against a frame in a ``python3 -I`` child. Frames cross
the boundary as Parquet. A cell reserves memory from the host's sandbox budget
before it starts; the limits bound a runaway script, not an attacker."""

from __future__ import annotations

import ast
import contextlib
import fcntl
import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from django.conf import settings

from overbae.services.datasets import examples, store, tables
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
CPU_SECONDS = 120
WALL_SECONDS = 300
_FILE_SIZE_BYTES = 2 * 1024**3
_TAIL_CHARS = 1200
_INSPECT_TAIL_CHARS = 4000
GIB = 1024**3
# Measured peak of a cell: 4-5.5x the frame's uncompressed Parquet bytes.
_FRAME_FACTOR = 5.5
_CHILD_BASE_BYTES = 256 * 1024**2
_MIN_RESERVATION = GIB
_GROWTH = 1.5
_BUDGET_SHARE = 0.6
_RESERVE_WAIT_SECONDS = 120
_WATCH_SECONDS = 0.1


@dataclass
class CellResult:
    path: Path | None = None
    error: str = ""
    stdout: str = ""
    ok: bool = False
    scratch: tempfile.TemporaryDirectory | None = field(default=None, repr=False)

    @property
    def rows(self) -> int:
        return store.row_count(self.path) if self.path else 0


def _gib(value: float) -> str:
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
    return settings.WORKSHOP_SANDBOX_MEMORY_BYTES or int(host_memory() * _BUDGET_SHARE)


def reservation(source: Path) -> int:
    loaded = tables.uncompressed_bytes(source)
    return max(_MIN_RESERVATION, int(loaded * _FRAME_FACTOR) + _CHILD_BASE_BYTES)


class SandboxBusyError(Exception):
    pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


@contextlib.contextmanager
def _ledger(budget: int) -> Iterator[dict]:
    path = Path(tempfile.gettempdir()) / f"overmind-sandbox-{budget}.json"
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
def _reserved(need: int, budget: int) -> Iterator[None]:
    token = uuid.uuid4().hex
    deadline = time.monotonic() + _RESERVE_WAIT_SECONDS
    while True:
        with _ledger(budget) as entries:
            if sum(int(e["bytes"]) for e in entries.values()) + need <= budget:
                entries[token] = {"pid": os.getpid(), "bytes": need}
                break
        if time.monotonic() > deadline:
            raise SandboxBusyError
        time.sleep(0.5)
    try:
        yield
    finally:
        with _ledger(budget) as entries:
            entries.pop(token, None)


def _rss(pid: int) -> int:
    try:
        with open(f"/proc/{pid}/status", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except OSError:
        pass
    return 0


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
# cap stops numpy from starting its thread pool. No RLIMIT_AS: allocators reserve
# far more address space than they use; the parent's watchdog bounds RSS.
for _limit, _ceiling in (
    (resource.RLIMIT_CPU, {CPU_SECONDS}),
    (resource.RLIMIT_FSIZE, {_FILE_SIZE_BYTES}),
):
    try:
        resource.setrlimit(_limit, (_ceiling, _ceiling))
    except (ValueError, OSError):
        pass
"""


_RUNNER = """\
import json, sys, traceback, runpy
script_path, in_path, out_path, mode, extra, examples_path, tables_path = sys.argv[1:8]
if extra:
    sys.path.insert(0, extra)
import pandas as pd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

pa.set_cpu_count(2)
pa.set_io_thread_count(2)
tables = runpy.run_path(tables_path)
kinds = {c["name"]: c["type"] for c in tables["read_manifest"](in_path)}
src = pq.read_table(in_path, pre_buffer=False, use_threads=False).to_pandas(
    self_destruct=True, split_blocks=True
)
for col, kind in kinds.items():
    if kind == "json" and col in src.columns:
        src[col] = src[col].map(lambda v: json.loads(v) if isinstance(v, str) else v)
ns = {"pd": pd, "pandas": pd, "np": np, "numpy": np, "source": src, "df": src.copy()}
ns["prepare_examples"] = runpy.run_path(examples_path)["prepare_examples"]
with open(script_path, encoding="utf-8") as fh:
    code = fh.read()
# The frames are the only files a cell touches: pandas and numpy IO is off.
def _blocked(*_a, **_k):
    raise RuntimeError("File and network IO is not available in a cell.")
for _name in [n for n in dir(pd) if n.startswith("read_")]:
    setattr(pd, _name, _blocked)
for _name in [n for n in dir(pd.DataFrame) if n.startswith("to_") and n not in ("to_dict", "to_records", "to_numpy", "to_string", "to_period", "to_timestamp", "to_xarray", "to_frame", "to_list")]:
    setattr(pd.DataFrame, _name, _blocked)
    if hasattr(pd.Series, _name):
        setattr(pd.Series, _name, _blocked)
for _name in ("load", "save", "savez", "savez_compressed", "savetxt", "loadtxt", "fromfile", "genfromtxt"):
    setattr(np, _name, _blocked)
try:
    exec(compile(code, "<cell>", "exec"), ns, ns)
except Exception:
    tb = traceback.format_exc().splitlines()
    keep = [
        line for line in tb
        if ("<cell>" in line or not line.startswith("  File")) and line.strip("~^ ")
    ]
    sys.stderr.write("\\n".join(keep[-12:]))
    raise SystemExit(3)
if mode == "inspect":
    raise SystemExit(0)
df = ns.get("df")
if df is None:
    raise SystemExit("The cell must leave a frame in df.")
if isinstance(df, pd.Series):
    df = df.to_frame()
if not isinstance(df, pd.DataFrame):
    raise SystemExit(f"df must be a pandas DataFrame, got {type(df).__name__}.")
df = df.copy()
if isinstance(df.columns, pd.MultiIndex):
    raise SystemExit("df has two levels of column names. Flatten them to one name per column.")
# A named index holds data (set_index, groupby); an unnamed one is only row labels.
named = [n for n in df.index.names if n is not None]
# Row identity survives a script that dropped the column but kept the index:
# same rows in the same order, or a filter that kept the original labels. A
# fresh RangeIndex after a reshape carries no identity.
if "source_row" not in df.columns and "source_row" in src.columns:
    fresh = df.index.equals(pd.RangeIndex(len(df)))
    if df.index.isin(src.index).all() and (not fresh or len(df) == len(src)):
        df.insert(0, "source_row", src.loc[df.index, "source_row"].values)
del src, ns
df = df.reset_index(drop=not named)
df.columns = [str(c) for c in df.columns]
repeated = sorted({c for c in df.columns if list(df.columns).count(c) > 1})
if repeated:
    raise SystemExit(f"df has more than one column named {repeated[0]!r}. Rename or drop one.")
if "source_row" in df.columns:
    df["source_row"] = pd.to_numeric(df["source_row"], errors="coerce").astype("Int64")
manifest = []
for col in df.columns:
    s = df[col]
    if s.dtype == object:
        filled = s.dropna()
        if not filled.empty and filled.map(lambda v: isinstance(v, (bool, np.bool_))).all():
            df[col] = s.astype("boolean")
            kind = "boolean"
        elif filled.map(lambda v: isinstance(v, (dict, list, tuple))).any():
            kind = "json"
        else:
            kind = "string"
    else:
        kind = tables["kind_of_series"](s)
    manifest.append({"name": col, "type": kind})
step = 256
with tables["TableWriter"](out_path, manifest) as writer:
    start = 0
    while start < len(df):
        chunk = df.iloc[start : start + step]
        piece = tables["table_from_columns"]({c: chunk[c].tolist() for c in chunk.columns}, manifest)
        writer.write_table(piece)
        start += step
        step = max(1, min(10_000, tables["BATCH_BYTES"] * step // max(piece.nbytes, 1)))
"""


def run(
    script: str, source: Path, *, library_cache: Path, produce_frame: bool = True
) -> CellResult:
    mode = "cell" if produce_frame else "inspect"
    noun = "cell" if produce_frame else "script"
    violations = audit(script, libraries.allowed_imports(library_cache))
    if violations:
        return CellResult(error="; ".join(violations[:5]))
    need, budget = reservation(source), memory_budget()
    if need > budget:
        return CellResult(
            error=f"This version needs about {_gib(need)} GiB of memory for a pandas {noun}; "
            f"this worker has {_gib(budget)} GiB."
        )
    try:
        with _reserved(need, budget):
            return _run_child(
                script, source, library_cache=library_cache, mode=mode, limit=int(need * _GROWTH)
            )
    except SandboxBusyError:
        return CellResult(
            error=f"Other cells were using this worker's memory for {_RESERVE_WAIT_SECONDS}s; "
            f"the {noun} did not start."
        )


def _run_child(
    script: str, source: Path, *, library_cache: Path, mode: str, limit: int
) -> CellResult:
    scratch = tempfile.TemporaryDirectory(prefix="cell_")
    result = _execute(script, source, Path(scratch.name), library_cache, mode, limit)
    if result.path is None:
        scratch.cleanup()
    else:
        result.scratch = scratch
    return result


def _execute(
    script: str, source: Path, tmp_path: Path, library_cache: Path, mode: str, limit: int
) -> CellResult:
    noun = "cell" if mode == "cell" else "script"
    stdout_tail = _TAIL_CHARS if mode == "cell" else _INSPECT_TAIL_CHARS
    script_path = tmp_path / "cell.py"
    runner_path = tmp_path / "_runner.py"
    out_path = tmp_path / "out.parquet"
    script_path.write_text(script, encoding="utf-8")
    runner_path.write_text(_LIMITS + _RUNNER, encoding="utf-8")
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONNOUSERSITE": "1",
        "OMP_NUM_THREADS": "2",
        "MALLOC_ARENA_MAX": "2",
        "HOME": str(tmp_path),
        "TIKTOKEN_CACHE_DIR": str(tmp_path / "tiktoken"),
    }
    stopped = ""
    text = {"encoding": "utf-8", "errors": "replace"}
    with (
        (tmp_path / "stdout").open("w+", **text) as out,
        (tmp_path / "stderr").open("w+", **text) as err,
    ):
        try:
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
                    str(Path(tables.__file__).resolve()),
                ],
                cwd=tmp_path,
                env=env,
                stdout=out,
                stderr=err,
                text=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return CellResult(error=f"The runner could not start: {exc}")
        deadline = time.monotonic() + WALL_SECONDS
        try:
            while True:
                try:
                    proc.wait(timeout=_WATCH_SECONDS)
                    break
                except subprocess.TimeoutExpired:
                    pass
                if time.monotonic() > deadline:
                    stopped = f"The {noun} ran longer than {WALL_SECONDS}s and was stopped."
                elif _rss(proc.pid) > limit:
                    stopped = (
                        f"The {noun} used more than {_gib(limit)} GiB of memory and was stopped."
                    )
                if stopped:
                    break
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        out.seek(0)
        err.seek(0)
        stdout, stderr = out.read(), err.read()
    if stopped:
        return CellResult(error=stopped, stdout=stdout[-stdout_tail:])
    if proc.returncode != 0:
        failure = (stderr or stdout or "").strip()[-_TAIL_CHARS:]
        if proc.returncode in (-9, 137):
            failure = failure or f"The {noun} used more memory or CPU than allowed."
        return CellResult(
            error=failure or f"The {noun} exited with code {proc.returncode}.",
            stdout=stdout[-stdout_tail:],
        )
    if mode == "inspect":
        return CellResult(stdout=stdout[-stdout_tail:], ok=True)
    if not out_path.exists():
        return CellResult(error="The cell's output could not be read.")
    return CellResult(path=out_path, stdout=stdout[-stdout_tail:], ok=True)
