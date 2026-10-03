"""Round-trip synthetic training data through an isolated CPU-only Modal volume."""

import argparse
import gzip
import hashlib
import json
import random
import tempfile
import time
import uuid
from pathlib import Path

import modal
from dotenv import load_dotenv

from modal_shared.training_data import file_digest, write_selection


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("receipt", type=Path)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--env-file", type=Path)
    args = parser.parse_args()
    if args.env_file:
        load_dotenv(args.env_file)
    name = "overmind-transfer-qualification-" + uuid.uuid4().hex[:12]
    result = {
        "fixture": "synthetic choices; generated locally, no source dataset",
        "rows": 8320,
        "environment": args.environment,
        "volume": name,
        "samples": [],
        "limits": "One observation per artifact; provider caching, ordering and SDK overhead are uncontrolled. No GPU calls, live volumes or training jobs are changed.",
    }
    args.receipt.parent.mkdir(parents=True, exist_ok=True)

    def save():
        args.receipt.write_text(json.dumps(result, indent=2) + "\n")

    save()
    volume = modal.Volume.from_name(name, create_if_missing=True, environment_name=args.environment)
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "synthetic.jsonl"
        rng = random.Random(73491)
        with source.open("w") as output:
            for i in range(result["rows"]):
                output.write(
                    json.dumps(
                        {
                            "decision": {
                                "state": f"Synthetic observation {i} "
                                + " ".join(str(rng.randrange(10000)) for _ in range(200)),
                                "question": "Select the synthetic category",
                                "kind": "choice",
                                "options": ["one", "two", "three"],
                                "target": [0.7, 0.2, 0.1],
                            }
                        }
                    )
                    + "\n"
                )
        compressed = root / "synthetic.jsonl.gz"
        started = time.perf_counter()
        compressed.write_bytes(gzip.compress(source.read_bytes(), compresslevel=1, mtime=0))
        result["compression_seconds"] = time.perf_counter() - started
        selection = root / "selection.keys"
        write_selection(source, selection)
        result["source_sha256"] = file_digest(source)
        files = [source, compressed, selection]
        rng.shuffle(files)
        for path in files:
            sample = {
                "artifact": path.name,
                "bytes": path.stat().st_size,
                "sha256": file_digest(path),
                "state": "uploading",
            }
            result["samples"].append(sample)
            save()
            started = time.perf_counter()
            with volume.batch_upload() as upload:
                upload.put_file(path, "/" + path.name)
            sample["upload_seconds"] = time.perf_counter() - started
            sample["state"] = "downloading"
            save()
            downloaded = root / (path.name + ".download")
            started = time.perf_counter()
            with downloaded.open("wb") as output:
                for chunk in volume.read_file("/" + path.name):
                    output.write(chunk)
            sample["download_seconds"] = time.perf_counter() - started
            sample["sha256_matches"] = file_digest(downloaded) == sample["sha256"]
            if path == compressed:
                sample["decompressed_sha256_matches"] = (
                    hashlib.sha256(gzip.decompress(downloaded.read_bytes())).hexdigest()
                    == result["source_sha256"]
                )
            assert sample["sha256_matches"] and sample.get("decompressed_sha256_matches", True)
            volume.remove_file("/" + path.name)
            sample["state"] = "verified_and_removed"
            save()
    result["passed"] = True
    save()
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
