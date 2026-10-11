"""Synthetic structured-output control for training-monitoring qualification."""

import argparse
import json
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    rows = [
        {
            "messages": [
                {
                    "role": "user",
                    "content": f'Control item {index}. Reply with exactly this JSON: {{"route":"review"}}. No explanation.',
                },
                {"role": "assistant", "content": '{"route":"review"}'},
            ],
            "synthetic": True,
            "purpose": "JSON output-contract qualification, not domain quality evidence",
        }
        for index in range(48)
    ]
    args.output.write_text("".join(json.dumps(row) + "\n" for row in rows))
    print(json.dumps({"path": str(args.output), "rows": len(rows), "synthetic": True}))


if __name__ == "__main__":
    main()
