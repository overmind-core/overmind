"""Isolated cell process; trusted file IO stays outside the cell namespace."""

import ast
import math
import runpy
import sys
import traceback
from pathlib import Path

import numpy as np
import pandas as pd


def finite(value):
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite(v) for v in value]
    return value


def normalize(frame, source):
    if isinstance(frame, pd.Series):
        frame = frame.to_frame()
    if not isinstance(frame, pd.DataFrame):
        raise ValueError("The cell must return a pandas DataFrame.")
    frame = frame.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        raise ValueError("df has two levels of column names. Flatten them to one name per column.")
    named = [n for n in frame.index.names if n is not None]
    if "source_row" not in frame.columns and "source_row" in source.columns:
        fresh = frame.index.equals(pd.RangeIndex(len(frame)))
        if frame.index.isin(source.index).all() and (not fresh or len(frame) == len(source)):
            frame.insert(0, "source_row", source.loc[frame.index, "source_row"].values)
    frame = frame.reset_index(drop=not named)
    frame.columns = [str(c) for c in frame.columns]
    if frame.columns.duplicated().any():
        name = frame.columns[frame.columns.duplicated()][0]
        raise ValueError(f"df has more than one column named {name!r}. Rename or drop one.")
    if "source_row" in frame:
        frame["source_row"] = pd.to_numeric(frame["source_row"], errors="coerce").astype("Int64")
    return frame


def block_io():
    def blocked(*args, **kwargs):
        raise RuntimeError("File and network IO is not available in a cell.")

    for name in [n for n in dir(pd) if n.startswith("read_")]:
        setattr(pd, name, blocked)
    allowed = {
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
    for name in [n for n in dir(pd.DataFrame) if n.startswith("to_") and n not in allowed]:
        setattr(pd.DataFrame, name, blocked)
        if hasattr(pd.Series, name):
            setattr(pd.Series, name, blocked)
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
        setattr(np, name, blocked)


def main():
    script_path, in_path, out_path, mode, extra, examples_path, store_path, sampling_path = (
        sys.argv[1:]
    )
    if extra:
        sys.path.insert(0, extra)
    storage = runpy.run_path(store_path)
    examples = runpy.run_path(examples_path)
    sample = runpy.run_path(sampling_path)["sample_frames"]
    code = Path(script_path).read_text()
    tree = ast.parse(code)
    batched = any(isinstance(n, ast.FunctionDef) and n.name == "transform_batch" for n in tree.body)
    inspected = mode == "inspect" and any(
        isinstance(n, ast.FunctionDef) and n.name == "inspect_batch" for n in tree.body
    )
    sampled = (
        len(tree.body) == 1
        and isinstance(tree.body[0], ast.Assign)
        and len(tree.body[0].targets) == 1
        and isinstance(tree.body[0].targets[0], ast.Name)
        and tree.body[0].targets[0].id == "df"
        and isinstance(tree.body[0].value, ast.Call)
        and isinstance(tree.body[0].value.func, ast.Name)
        and tree.body[0].value.func.id == "sample_rows"
    )
    namespace = {
        "pd": pd,
        "pandas": pd,
        "np": np,
        "numpy": np,
        "prepare_examples": examples["prepare_examples"],
    }
    if sampled:
        namespace["sample_rows"] = lambda **kwargs: sample(
            lambda: storage["iter_frames"](Path(in_path)), examples["native_decision"], **kwargs
        )
    elif not batched and not inspected:
        source = storage["read_frame"](Path(in_path))
        namespace.update(source=source, df=source.copy())
    block_io()
    exec(compile(tree, "<cell>", "exec"), namespace, namespace)
    if mode == "inspect":
        if inspected:
            offset = 0
            for batch in storage["iter_frames"](Path(in_path)):
                batch.index = pd.RangeIndex(offset, offset + len(batch))
                offset += len(batch)
                namespace["inspect_batch"](batch)
            if "finish_inspection" in namespace:
                namespace["finish_inspection"]()
            return
        if batched or sampled:
            raise ValueError(
                "Use a batch cell to return results; inspect executes a whole-frame script."
            )
        return
    last = None

    def records():
        nonlocal last
        if sampled:
            for batch in namespace["df"]:
                last = normalize(batch, batch)
                yield from (finite(r) for r in last.to_dict(orient="records"))
            return
        if not batched:
            last = normalize(namespace.get("df"), source)
            yield from (finite(r) for r in last.to_dict(orient="records"))
            return
        offset = 0
        for batch in storage["iter_frames"](Path(in_path)):
            batch.index = pd.RangeIndex(offset, offset + len(batch))
            offset += len(batch)
            last = normalize(namespace["transform_batch"](batch.copy()), batch)
            yield from (finite(r) for r in last.to_dict(orient="records"))

    storage["write_rows"](Path(out_path), records())
    if last is not None and not storage["row_count"](Path(out_path)):
        storage["write_frame"](Path(out_path), last.iloc[:0])


if __name__ == "__main__":
    try:
        main()
    except Exception:
        lines = traceback.format_exc().splitlines()
        sys.stderr.write("\n".join(lines[-12:]))
        raise SystemExit(3) from None
