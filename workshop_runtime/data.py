import argparse
import hashlib
import json
import math
import subprocess
import sys
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from modal_shared.decisions import decision_line


def iter_jsonl(path):
    # Unicode separators inside JSON strings are data, not JSONL record boundaries.
    with Path(path).open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def read_frame(path):
    path = Path(path)
    if path.suffix == ".parquet":
        frame = pd.read_parquet(path, dtype_backend="pyarrow")
        metadata = pq.read_schema(path).metadata or {}
        for column in json.loads(metadata.get(b"overmind.columns", b"[]")):
            if column["type"] == "json":
                frame[column["name"]] = pd.Series(
                    [
                        json.loads(value) if isinstance(value, str) else value
                        for value in frame[column["name"]]
                    ],
                    index=frame.index,
                    dtype=object,
                )
        return frame
    if path.suffix in {".jsonl", ".ndjson"}:
        return pd.DataFrame(iter_jsonl(path), dtype=object)
    if path.suffix == ".json":
        return pd.DataFrame(json.loads(path.read_text()), dtype=object)
    if path.suffix in {".xlsx", ".xlsm"}:
        return pd.read_excel(path, engine="openpyxl")
    if path.suffix in {".csv", ".tsv"}:
        return pd.read_csv(path, sep="\t" if path.suffix == ".tsv" else ",")
    raise ValueError("Supported formats: CSV, TSV, JSON records, JSONL, Parquet, XLSX, XLSM.")


def json_value(value):
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, np.ndarray):
        return [json_value(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, float) and math.isnan(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def records(frame):
    return [json_value(row) for row in frame.to_dict(orient="records")]


def replay_inputs(rows):
    return [
        {
            k: v
            for k, v in row.items()
            if not k.startswith("_overmind_") or k == "_overmind_parent_rows"
        }
        for row in rows
    ]


def profile(path, target=None):
    frame = read_frame(path)
    rows = records(frame)
    encoded = [
        json.dumps(
            {
                key: value
                for key, value in row.items()
                if key not in {"source_row", "_overmind_provenance"}
            },
            sort_keys=True,
        )
        for row in rows
    ]
    result = {
        "rows": len(frame),
        "columns": {
            name: {
                "dtype": str(frame[name].dtype),
                "missing": int(frame[name].isna().sum()),
                "distinct": len({json.dumps(row[name], sort_keys=True) for row in rows}),
            }
            for name in frame.columns
        },
        "duplicate_rows": len(encoded) - len(set(encoded)),
        "source_sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
    }
    if target:
        if target not in frame:
            raise ValueError("The explicit target column is absent.")
        result["target"] = {
            "column": target,
            "counts": [
                {"value": json.loads(value), "rows": count}
                for value, count in Counter(json.dumps(row[target]) for row in rows).items()
            ],
        }
        result["exact_target_copies"] = [
            name for name in frame.columns if name != target and frame[name].equals(frame[target])
        ]
    return result


def stage(source, destination):
    frame = read_frame(source)
    if "source_row" not in frame:
        raise ValueError(
            "Export the pinned platform cell: source_row is required; row positions are not inferred."
        )
    with Path(destination).open("w") as output:
        for row in replay_inputs(records(frame)):
            output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    return {"rows": len(frame), "output": str(destination)}


def validate(source, output, *, decision=False):
    original, transformed = read_frame(source), read_frame(output)
    if "source_row" not in original or "source_row" not in transformed:
        raise ValueError("source_row is required in source and output.")
    before, after = original["source_row"].tolist(), transformed["source_row"].tolist()
    if any(type(value) is not int for value in before + after):
        raise ValueError("source_row must contain integer identities.")
    if (
        len(set(before)) != len(before)
        or len(set(after)) != len(after)
        or set(before) != set(after)
    ):
        raise ValueError("This row-preserving check requires each source identity exactly once.")
    if decision:
        for row in records(transformed):
            decision_line(row)
    return {
        "rows": len(transformed),
        "lineage": "preserved",
        "decision_contract": "valid" if decision else "not_checked",
        "semantic_correctness": "not_checked",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Offline Data Workshop exploration and pipeline checks."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    p = commands.add_parser("profile")
    p.add_argument("input")
    p.add_argument("--target")
    p = commands.add_parser("stage")
    p.add_argument("input")
    p.add_argument("output")
    p = commands.add_parser("validate")
    p.add_argument("source")
    p.add_argument("output")
    p.add_argument("--decision", action="store_true")
    p = commands.add_parser("run-step")
    p.add_argument("script")
    p.add_argument("input")
    p.add_argument("output")
    p.add_argument("parameters")
    args = parser.parse_args()
    if args.command == "profile":
        result = profile(args.input, args.target)
    elif args.command == "stage":
        result = stage(args.input, args.output)
    elif args.command == "validate":
        result = validate(args.source, args.output, decision=args.decision)
    else:
        subprocess.run(
            [sys.executable, args.script, args.input, args.output, args.parameters], check=True
        )
        result = {"output": args.output, "rows": len(read_frame(args.output))}
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
