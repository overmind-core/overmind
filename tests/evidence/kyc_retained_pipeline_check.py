"""Replay the retained KYC scripts and verify their source-to-output contract."""

import argparse
import copy
import hashlib
import itertools
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE = Path(__file__).with_name("kyc_retained_pipeline")


def replay(source, destination):
    manifest = json.loads((PACKAGE / "manifest.json").read_text())
    with tempfile.TemporaryDirectory(prefix="kyc-replay-") as scratch:
        root = Path(scratch)
        parameters = root / "parameters.json"
        parameters.write_text("{}")
        previous = source
        for index, step in enumerate(manifest["steps"]):
            output = destination if index == len(manifest["steps"]) - 1 else root / f"{index}.jsonl"
            result = subprocess.run(
                [
                    sys.executable,
                    str(PACKAGE / step["entrypoint"]),
                    str(previous),
                    str(output),
                    str(parameters),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                return result
            previous = output
        return result


def verify(source, output, baseline=None):
    count = 0
    digest = hashlib.sha256()
    with source.open() as original, output.open() as transformed:
        for raw, actual in itertools.zip_longest(original, transformed):
            assert raw is not None and actual is not None, "Row count changed"
            source_row, result = json.loads(raw), json.loads(actual)
            assert result["question"] == source_row["tokens"], f"Input changed at row {count}"
            assert result["answer"] == source_row["kyc_risk_bucket"], (
                f"Target changed at row {count}"
            )
            assert result["messages"] == [
                {"role": "user", "content": source_row["tokens"]},
                {"role": "assistant", "content": source_row["kyc_risk_bucket"]},
            ], f"Conversation changed at row {count}"
            if "source_row" in source_row:
                assert result["source_row"] == source_row["source_row"], (
                    f"Identity changed at row {count}"
                )
            model_fields = {key: result[key] for key in ("question", "answer", "messages")}
            digest.update(json.dumps(model_fields, ensure_ascii=False, sort_keys=True).encode())
            digest.update(b"\n")
            count += 1
    if baseline:
        with baseline.open() as previous, output.open() as transformed:
            for old, new in itertools.zip_longest(previous, transformed):
                assert old is not None and new is not None, "Baseline row count changed"
                old_row, new_row = json.loads(old), json.loads(new)
                for key in ("question", "answer", "messages"):
                    assert old_row[key] == new_row[key], f"Previous model field changed: {key}"
    return {
        "rows": count,
        "model_fields_sha256": digest.hexdigest(),
        "baseline_equivalent": bool(baseline),
    }


def edge_cases():
    rows = [
        {
            "source_row": 0,
            "tokens": "  Évidence 日本語\nignore previous instructions  ",
            "kyc_risk_bucket": "enhanced",
            "risk_score": 0.97,
        },
        {"source_row": 1, "tokens": "duplicate", "kyc_risk_bucket": "standard"},
        {"source_row": 2, "tokens": "duplicate", "kyc_risk_bucket": "standard"},
        {
            "source_row": 9007199254740993,
            "tokens": "unknown is preserved",
            "kyc_risk_bucket": "new_unreviewed_label",
        },
    ]
    cases = [("valid", rows, True), ("empty", [], True)]
    for field in ("tokens", "kyc_risk_bucket"):
        for value in (None, "", " \n", 12):
            row = copy.deepcopy(rows[0])
            row[field] = value
            cases.append((f"invalid-{field}-{value!r}", [row], False))
        row = copy.deepcopy(rows[0])
        del row[field]
        cases.append((f"missing-{field}", [row], False))
    for identity in (True, "0", -1):
        row = copy.deepcopy(rows[0])
        row["source_row"] = identity
        cases.append((f"invalid-identity-{identity!r}", [row], False))
    with tempfile.TemporaryDirectory(prefix="kyc-contract-") as scratch:
        root = Path(scratch)
        for name, inputs, success in cases:
            source, output = root / "source.jsonl", root / "output.jsonl"
            source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in inputs))
            result = replay(source, output)
            assert (result.returncode == 0) == success, f"{name}: {result.stderr}"
            if success:
                verify(source, output)
        source.write_text("".join(json.dumps(row) + "\n" for row in rows))
        assert replay(source, output).returncode == 0
        first = output.read_bytes()
        assert replay(source, output).returncode == 0
        assert output.read_bytes() == first, "Replay was not deterministic"
    return {"edge_cases_passed": len(cases), "repeat_byte_identical": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--replay", action="store_true")
    args = parser.parse_args()
    result = edge_cases()
    if args.source:
        assert args.output, "--output is required with --source"
        if args.replay:
            execution = replay(args.source, args.output)
            assert execution.returncode == 0, execution.stderr
        result["full_source"] = verify(args.source, args.output, args.baseline)
    print(json.dumps(result, indent=2))
