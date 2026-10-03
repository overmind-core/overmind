import argparse
import gzip
import hashlib
import json
import shutil
import tempfile
import time
from pathlib import Path

from modal_shared.training_data import file_digest, write_selection

parser = argparse.ArgumentParser()
parser.add_argument("source", type=Path)
parser.add_argument("result", type=Path)
args = parser.parse_args()
with tempfile.TemporaryDirectory(prefix="transfer-measure-") as directory:
    keys = Path(directory) / "selection.keys"
    compressed = Path(directory) / "source.jsonl.gz"
    started = time.perf_counter()
    selection = write_selection(args.source, keys)
    selection_seconds = time.perf_counter() - started
    started = time.perf_counter()
    with args.source.open("rb") as source, gzip.open(compressed, "wb", compresslevel=1) as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)
    compression_seconds = time.perf_counter() - started
    raw_digest = file_digest(args.source)
    roundtrip = hashlib.sha256()
    with gzip.open(compressed, "rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            roundtrip.update(block)
    assert roundtrip.hexdigest() == raw_digest
    before = args.source.stat().st_size
    result = {
        "source": str(args.source),
        "source_sha256": raw_digest,
        "rows": selection["rows"],
        "raw_bytes": before,
        "initial_compressed_bytes": compressed.stat().st_size,
        "selection_bytes": keys.stat().st_size,
        "initial_compression_seconds": compression_seconds,
        "selection_build_seconds": selection_seconds,
        "initial_reduction_percent": 100 * (1 - compressed.stat().st_size / before),
        "handoff_reduction_percent": 100 * (1 - keys.stat().st_size / before),
        "initial_roundtrip_sha256_match": True,
        "selection_sha256": selection["sha256"],
        "network_transfer_timing_measured": False,
    }
args.result.write_text(json.dumps(result, indent=2) + "\n")
print(json.dumps(result, indent=2))
