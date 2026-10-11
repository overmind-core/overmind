"""Measure bounded Parquet staging in a fresh process using generated wide rows."""

import argparse
import json
import resource
import sys
import tempfile
import time
from pathlib import Path

from overbae.services.datasets import store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    started = time.monotonic()
    count, width = 2048, 256 * 1024
    with tempfile.TemporaryDirectory(prefix="workshop-memory-") as directory:
        path = Path(directory) / "rows.parquet"
        store.write_rows(
            path,
            (
                {
                    "source_row": i,
                    "text": f"{i:08d}" + "x" * width,
                    "decision": {"state": str(i), "target_probabilities": [0.25, 0.75]},
                    "precision": 2**63 + i,
                }
                for i in range(count)
            ),
        )
        for i, row in enumerate(store.iter_rows(path)):
            assert row["source_row"] == i
            assert row["text"] == f"{i:08d}" + "x" * width
            assert row["precision"] == 2**63 + i
            assert row["decision"]["target_probabilities"] == [0.25, 0.75]
        assert i + 1 == count
        measured = 0
        for frame in store.iter_frames(path):
            measured += len(frame)
            assert frame["precision"].tolist() == [2**63 + i for i in frame["source_row"]]
        assert measured == count
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    peak_mib = peak / (1024**2 if sys.platform == "darwin" else 1024)
    result = {
        "rows": count,
        "text_bytes_per_row": width + 8,
        "peak_rss_mib": round(peak_mib, 2),
        "seconds": round(time.monotonic() - started, 2),
        "limit_mib": 768,
        "passed": peak_mib < 768,
    }
    Path(args.output).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)
    assert result["passed"], result


if __name__ == "__main__":
    main()
