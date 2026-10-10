#!/usr/bin/env python3
"""Generate review-only Workshop batch scaling; never change AWS resources."""

import argparse
import json
from pathlib import Path

if __package__:
    from .queue_scaling import scaling_plan
else:
    from queue_scaling import scaling_plan

ROOT = Path(__file__).resolve().parents[1]


def capacity_plan(*, cluster, min_capacity=1, max_capacity=6, alarm_topic_arn=None):
    command = json.loads((ROOT / "docker/worker-topology.json").read_text())["celery-batch-worker"]
    slots = int(next(arg.split("=", 1)[1] for arg in command if arg.startswith("--concurrency=")))
    return scaling_plan(
        cluster=cluster,
        queue="batch",
        service="celery-batch-worker",
        slots=slots,
        min_capacity=min_capacity,
        max_capacity=max_capacity,
        alarm_topic_arn=alarm_topic_arn,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", required=True)
    parser.add_argument("--min-capacity", type=int, default=1)
    parser.add_argument("--max-capacity", type=int, default=6)
    parser.add_argument("--alarm-topic-arn")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    plan = capacity_plan(
        cluster=args.cluster,
        min_capacity=args.min_capacity,
        max_capacity=args.max_capacity,
        alarm_topic_arn=args.alarm_topic_arn,
    )
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in plan.items():
        (args.output / name).write_text(json.dumps(value, indent=2) + "\n")
    print(f"Wrote review-only workshop plan to {args.output}; no AWS resources changed.")


if __name__ == "__main__":
    main()
