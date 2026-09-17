"""Execute one cell body against a frame in a ``python3 -I`` child under cpu,
memory and file-size rlimits. Frames cross the boundary as Parquet. The limits
bound a runaway script, not an attacker."""

from __future__ import annotations

import ast
import contextlib
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from overbae.services.datasets import store
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
_ADDRESS_SPACE_BYTES = 6 * 1024**3
_FILE_SIZE_BYTES = 2 * 1024**3
_TAIL_CHARS = 1200
_INSPECT_TAIL_CHARS = 4000


@dataclass
class CellResult:
    frame: pd.DataFrame | None
    error: str = ""
    stdout: str = ""
    ok: bool = False


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


def _limits():  # pragma: no cover — runs in the child
    def apply() -> None:
        import resource

        # No RLIMIT_NPROC: it counts every process of the uid, so any useful
        # cap stops numpy from starting its thread pool.
        for name, ceiling in (
            (resource.RLIMIT_CPU, (CPU_SECONDS, CPU_SECONDS)),
            (resource.RLIMIT_AS, (_ADDRESS_SPACE_BYTES, _ADDRESS_SPACE_BYTES)),
            (resource.RLIMIT_FSIZE, (_FILE_SIZE_BYTES, _FILE_SIZE_BYTES)),
        ):
            with contextlib.suppress(ValueError, OSError):
                resource.setrlimit(name, ceiling)

    return apply


_RUNNER = """\
import json, math, sys, traceback
extra = sys.argv[6] if len(sys.argv) > 6 else ""
if extra:
    sys.path.insert(0, extra)
import pandas as pd
import numpy as np

script_path, in_path, out_path, kinds_path, mode = sys.argv[1:6]
src = pd.read_parquet(in_path)
kinds = json.load(open(kinds_path, encoding="utf-8"))
for col, kind in kinds.items():
    if kind == "json" and col in src.columns:
        src[col] = src[col].map(lambda v: json.loads(v) if isinstance(v, str) else v)
ns = {"pd": pd, "pandas": pd, "np": np, "numpy": np, "source": src, "df": src.copy()}
with open(script_path, encoding="utf-8") as fh:
    code = fh.read()
# The frames are the only files a cell touches: pandas and numpy IO is off.
_write_parquet = pd.DataFrame.to_parquet
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
df = df.reset_index(drop=not named)
df.columns = [str(c) for c in df.columns]
repeated = sorted({c for c in df.columns if list(df.columns).count(c) > 1})
if repeated:
    raise SystemExit(f"df has more than one column named {repeated[0]!r}. Rename or drop one.")
if "source_row" in df.columns:
    df["source_row"] = pd.to_numeric(df["source_row"], errors="coerce").astype("Int64")
# A NaN nested in a container must become null; json.dumps would write a bare NaN token.
def _finite(v):
    if isinstance(v, (float, np.floating)):
        return float(v) if math.isfinite(v) else None
    if isinstance(v, dict):
        return {k: _finite(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_finite(x) for x in v]
    return v

def _as_json(v):
    return json.dumps(_finite(v), ensure_ascii=False, default=str, sort_keys=True, allow_nan=False)

out_kinds = {}
for col in df.columns:
    s = df[col]
    if s.dtype == object and s.map(lambda v: isinstance(v, (dict, list, tuple))).any():
        df[col] = s.map(lambda v: _as_json(v) if isinstance(v, (dict, list, tuple)) else (None if v is None or (isinstance(v, float) and v != v) else _as_json(v)))
        out_kinds[col] = "json"
    elif s.dtype == object:
        df[col] = s.map(lambda v: None if v is None or (isinstance(v, float) and v != v) else str(v) if not isinstance(v, str) else v)
        out_kinds[col] = "string"
_write_parquet(df, out_path, index=False)
json.dump(out_kinds, open(out_path + ".kinds", "w", encoding="utf-8"))
"""


def run(
    script: str, source: Path, *, library_cache: Path, produce_frame: bool = True
) -> CellResult:
    mode = "cell" if produce_frame else "inspect"
    noun = "cell" if produce_frame else "script"
    stdout_tail = _TAIL_CHARS if produce_frame else _INSPECT_TAIL_CHARS
    violations = audit(script, libraries.allowed_imports(library_cache))
    if violations:
        return CellResult(None, error="; ".join(violations[:5]))
    with tempfile.TemporaryDirectory(prefix="cell_") as tmp:
        tmp_path = Path(tmp)
        script_path = tmp_path / "cell.py"
        runner_path = tmp_path / "_runner.py"
        kinds_path = tmp_path / "in.kinds"
        out_path = tmp_path / "out.parquet"
        script_path.write_text(script, encoding="utf-8")
        runner_path.write_text(_RUNNER, encoding="utf-8")
        kinds_path.write_text(
            json.dumps({c["name"]: c["type"] for c in store.read_manifest(source)}),
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
            proc = subprocess.run(  # noqa: S603 — audited script, jailed child
                [
                    sys.executable,
                    "-I",
                    str(runner_path),
                    str(script_path),
                    str(source),
                    str(out_path),
                    str(kinds_path),
                    mode,
                    str(library_cache) if library_cache.exists() else "",
                ],
                cwd=tmp,
                env=env,
                capture_output=True,
                text=True,
                timeout=WALL_SECONDS,
                preexec_fn=_limits() if os.name == "posix" else None,
            )
        except subprocess.TimeoutExpired:
            return CellResult(
                None, error=f"The {noun} ran longer than {WALL_SECONDS}s and was stopped."
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return CellResult(None, error=f"The runner could not start: {exc}")
        if proc.returncode != 0:
            failure = (proc.stderr or proc.stdout or "").strip()[-_TAIL_CHARS:]
            if proc.returncode in (-9, 137):
                failure = failure or f"The {noun} used more memory or CPU than allowed."
            return CellResult(
                None,
                error=failure or f"The {noun} exited with code {proc.returncode}.",
                stdout=proc.stdout[-stdout_tail:],
            )
        if not produce_frame:
            return CellResult(None, stdout=proc.stdout[-stdout_tail:], ok=True)
        try:
            frame = pd.read_parquet(out_path)
            kinds = json.loads((tmp_path / "out.parquet.kinds").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            return CellResult(None, error=f"The cell's output could not be read: {exc}")
        for col, kind in kinds.items():
            if kind == "json" and col in frame.columns:
                frame[col] = frame[col].map(lambda v: json.loads(v) if isinstance(v, str) else v)
        return CellResult(frame, stdout=proc.stdout[-stdout_tail:], ok=True)
