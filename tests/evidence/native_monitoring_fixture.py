"""Deterministic synthetic targets for native monitoring qualification, not task evidence."""

import argparse
import json
from pathlib import Path


def rows():
    for index in range(48):
        if index % 2:
            target = {
                "kind": "score",
                "options": ["low", "middle", "high"],
                "target_mean": float(index % 3),
                "option_values": [0, 1, 2],
                "target_semantics": "ordinal_mean",
            }
        else:
            target = {
                "kind": "choice",
                "options": ["red", "blue"],
                "target_probabilities": [0.25, 0.75],
                "target_semantics": "teacher_distribution",
            }
        yield {
            "decision": {
                "state": f"Synthetic monitoring control observation {index}.",
                "question": "Select the fixture outcome.",
                "weight": 1 + index % 3,
                "target_provenance": {
                    "producer": "native_monitoring_fixture.py",
                    "synthetic": True,
                },
                **target,
            }
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    records = list(rows())
    with args.output.open("x") as stream:
        for row in records:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(json.dumps({"rows": len(records), "output": str(args.output), "synthetic": True}))
