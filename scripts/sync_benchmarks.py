#!/usr/bin/env python3
"""Regenerate the committed benchmark artifact from the upstream leaderboards.

uv run python scripts/sync_benchmarks.py --dry-run
uv run python scripts/sync_benchmarks.py
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from overbae.services.benchmarks import artifact  # noqa: E402
from overbae.services.benchmarks.schema import BenchmarkArtifact  # noqa: E402
from overbae.services.benchmarks.sync import build as sync  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="validate and diff, write nothing")
    parser.add_argument("--out", type=Path, default=artifact.ARTIFACT_PATH, help="artifact path")
    args = parser.parse_args(argv)

    previous = artifact.load(refresh=True)
    result = sync.build()

    _summary(result)
    _diff(previous, result.payload["models"])

    if args.dry_run:
        print(f"\nwrote        nothing (--dry-run), target {args.out}")
        return 0
    sync.write(result.payload, args.out)
    print(f"\nwrote        {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")
    return 0


def _summary(result: sync.BuildResult) -> None:
    catalog = len(result.covered) + len(result.uncovered)
    tiers = Counter(
        match.match_type for match in result.matches if match.model_key in result.payload["models"]
    )
    overlaps = Counter(benchmark for _, benchmark in result.overlaps)

    print(f"models       {len(result.covered)} of {catalog} covered")
    print(f"benchmarks   {len(result.benchmarks)}")
    print(f"per model    {result.mean_benchmarks:.1f} mean")
    print(f"join         {_tally(tiers)}")
    print(f"overlaps     {_tally(overlaps)} resolved to the leaderboard")
    print(f"dropped      {_tally(result.untagged_dropped)} untagged upstream")
    if result.hf.fetch_failures:
        print(f"hub errors   {', '.join(sorted(result.hf.fetch_failures))}")
    print(f"no data      {', '.join(result.uncovered) or 'none'}")


def _tally(counts: Mapping[str, int]) -> str:
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return " · ".join(f"{label} {count}" for label, count in ranked) or "none"


def _diff(previous: BenchmarkArtifact, models: Mapping[str, list[dict[str, Any]]]) -> None:
    added = sorted(set(models) - set(previous.models))
    removed = sorted(set(previous.models) - set(models))
    moved = [
        (key, len(previous.models[key]), len(models[key]))
        for key in sorted(set(models) & set(previous.models))
        if len(previous.models[key]) != len(models[key])
    ]
    stamp = previous.generated_at if previous.models else "empty placeholder"
    print(f"\nvs committed {stamp}")
    print(f"  added      {', '.join(added) or 'none'}")
    print(f"  removed    {', '.join(removed) or 'none'}")
    for key, was, now in moved:
        print(f"  changed    {key} {was} → {now}")


if __name__ == "__main__":
    raise SystemExit(main())
