import json
import linecache
import resource
import sys
import traceback
from pathlib import Path


def _limit(cpu_seconds: int, file_bytes: int, processes: int, descriptors: int) -> None:
    # No RLIMIT_AS: allocators reserve far more address space than they use; the
    # server's watchdog bounds the process group's RSS instead.
    limits = [
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, file_bytes),
        (resource.RLIMIT_NOFILE, descriptors),
    ]
    if processes:
        limits.append((resource.RLIMIT_NPROC, processes))
    for which, ceiling in limits:
        resource.setrlimit(which, (ceiling, ceiling))


def _blocked(*_args, **_kwargs):
    raise RuntimeError("File and network IO is not available in a script.")


def _close_io(pd, np) -> None:
    for name in [n for n in dir(pd) if n.startswith("read_")]:
        setattr(pd, name, _blocked)
    kept = {
        "to_dict",
        "to_records",
        "to_numpy",
        "to_string",
        "to_period",
        "to_timestamp",
        "to_xarray",
        "to_frame",
        "to_list",
    }
    for name in [n for n in dir(pd.DataFrame) if n.startswith("to_") and n not in kept]:
        setattr(pd.DataFrame, name, _blocked)
        if hasattr(pd.Series, name):
            setattr(pd.Series, name, _blocked)
    for name in (
        "load",
        "save",
        "savez",
        "savez_compressed",
        "savetxt",
        "loadtxt",
        "fromfile",
        "genfromtxt",
    ):
        setattr(np, name, _blocked)


def _frame(df, src, pd, tables):
    if df is None:
        raise SystemExit("The script must leave a frame in df.")
    if isinstance(df, pd.Series):
        df = df.to_frame()
    if not isinstance(df, pd.DataFrame):
        raise SystemExit(f"df must be a pandas DataFrame, got {type(df).__name__}.")
    df = df.copy()
    if isinstance(df.columns, pd.MultiIndex):
        raise SystemExit("df has two levels of column names. Flatten them to one name per column.")
    # A named index holds data (set_index, groupby); an unnamed one is only row labels.
    named = [n for n in df.index.names if n is not None]
    # Row identity survives a script that dropped the column but kept the row labels
    # (main gives the input a plain integer index for this). A RangeIndex means the rows
    # were renumbered (reset_index, a new frame), and positions carry no identity.
    if tables.SOURCE_ROW not in df.columns and tables.SOURCE_ROW in src.columns:
        renumbered = isinstance(df.index, pd.RangeIndex)
        if not renumbered and df.index.isin(src.index).all():
            df.insert(0, tables.SOURCE_ROW, src.loc[df.index, tables.SOURCE_ROW].values)
    return df.reset_index(drop=not named)


def _write(df, out_path: str, pd, np, tables) -> None:
    df.columns = [str(c) for c in df.columns]
    repeated = sorted({c for c in df.columns if list(df.columns).count(c) > 1})
    if repeated:
        raise SystemExit(f"df has more than one column named {repeated[0]!r}. Rename or drop one.")
    if tables.SOURCE_ROW in df.columns:
        df[tables.SOURCE_ROW] = pd.to_numeric(df[tables.SOURCE_ROW], errors="coerce").astype(
            "Int64"
        )
    manifest = []
    for col in df.columns:
        series = df[col]
        filled = series.dropna() if series.dtype == object else None
        if (
            filled is not None
            and not filled.empty
            and filled.map(lambda v: isinstance(v, bool | np.bool_)).all()
        ):
            df[col] = series.astype("boolean")
            kind = "boolean"
        else:
            kind = tables.kind_of_series(series)
        manifest.append({"name": col, "type": kind})
    step = 256
    with tables.TableWriter(out_path, manifest) as writer:
        start = 0
        while start < len(df):
            chunk = df.iloc[start : start + step]
            piece = tables.table_from_columns(
                {c: chunk[c].tolist() for c in chunk.columns}, manifest
            )
            writer.write_table(piece)
            start += step
            step = max(1, min(10_000, tables.BATCH_BYTES * step // max(piece.nbytes, 1)))


def _read(path: str, pd, np, pq, tables):
    kinds = {c["name"]: c["type"] for c in tables.read_manifest(path)}
    frame = pq.read_table(path, pre_buffer=False, use_threads=False).to_pandas(
        self_destruct=True, split_blocks=True
    )
    frame.index = pd.Index(np.arange(len(frame)))
    for col, kind in kinds.items():
        if kind == "json" and col in frame.columns:
            frame[col] = frame[col].map(lambda v: json.loads(v) if isinstance(v, str) else v)
    return frame


def main(argv: list[str]) -> None:
    limits, script_path, in_path, out_path, mode, attachment_path = argv
    _limit(*json.loads(limits))

    import numpy as np
    import pandas as pd
    import pyarrow as pa
    import pyarrow.parquet as pq

    # -I keeps the working directory off sys.path; import the code next to this file,
    # not a copy installed in the environment.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from sandbox import examples, tables

    pa.set_cpu_count(2)
    pa.set_io_thread_count(2)
    src = _read(in_path, pd, np, pq, tables)
    attachment = _read(attachment_path, pd, np, pq, tables) if attachment_path else None
    namespace = {
        "pd": pd,
        "pandas": pd,
        "np": np,
        "numpy": np,
        "source": src,
        "df": src.copy(),
        "attachment": attachment,
        "prepare_examples": examples.prepare_examples,
    }
    with open(script_path, encoding="utf-8") as fh:
        code = fh.read()
    _close_io(pd, np)
    linecache.cache["<script>"] = (len(code), None, code.splitlines(keepends=True), "<script>")
    try:
        exec(compile(code, "<script>", "exec"), namespace, namespace)
    except Exception as exc:
        frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<script>"]
        lines = [*traceback.format_list(frames), *traceback.format_exception_only(exc)]
        sys.stderr.write("".join(lines).rstrip())
        raise SystemExit(3) from None
    if mode == "inspect":
        return
    df = _frame(namespace.pop("df", None), src, pd, tables)
    del src, attachment
    namespace.clear()
    _write(df, out_path, pd, np, tables)


if __name__ == "__main__":
    main(sys.argv[1:])
