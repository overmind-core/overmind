import argparse
import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path

import pyarrow.parquet as pq
import requests

SOURCES = {
    "banking77": (
        "mteb/banking77",
        "18072d2685ea682290f7b8924d94c62acc19c0b2",
        {"train": ("train.jsonl", 10003), "test": ("test.jsonl", 3080)},
    ),
    "sst5": (
        "SetFit/sst5",
        "e51bdcd8cd3a30da231967c1a249ba59361279a3",
        {
            "train": ("train.jsonl", 8544),
            "validation": ("dev.jsonl", 1101),
            "test": ("test.jsonl", 2210),
        },
    ),
    "boolq": (
        "google/boolq",
        "35b264d03638db9f4ce671b711558bf7ff0f80d5",
        {
            "train": ("data/train-00000-of-00001.parquet", 9427),
            "validation": ("data/validation-00000-of-00001.parquet", 3270),
        },
    ),
}


def fetch(url, path):
    response = requests.get(url, timeout=90)
    response.raise_for_status()
    path.write_bytes(response.content)
    return response.content


def main(destination):
    destination.mkdir(parents=True, exist_ok=True)
    manifest = {}
    banking_info = json.loads(
        fetch(
            "https://huggingface.co/datasets/PolyAI/banking77/resolve/90d4e2ee5521c04fc1488f065b8b083658768c57/dataset_infos.json",
            destination / "banking77-dataset_infos.json",
        )
    )["default"]
    options = banking_info["features"]["label"]["names"]
    for benchmark, (repo, revision, files) in SOURCES.items():
        directory = destination / benchmark
        directory.mkdir(exist_ok=True)
        rows, receipts = [], []
        for split, (file, expected) in files.items():
            url = f"https://huggingface.co/datasets/{repo}/resolve/{revision}/{file}"
            path = directory / Path(file).name
            raw = fetch(url, path)
            records = (
                pq.read_table(path).to_pylist()
                if file.endswith(".parquet")
                else [json.loads(line) for line in raw.splitlines()]
            )
            assert len(records) == expected, (benchmark, split, len(records))
            receipt = {
                "repo": repo,
                "revision": revision,
                "file": file,
                "split": split,
                "sha256": hashlib.sha256(raw).hexdigest(),
                "bytes": len(raw),
                "rows": len(records),
                "url": url,
            }
            if benchmark == "banking77":
                original_url = f"https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/master/banking_data/{split}.csv"
                original = fetch(original_url, directory / f"{split}.csv")
                assert (
                    hashlib.sha256(original).hexdigest()
                    == banking_info["download_checksums"][original_url]["checksum"]
                )
                original_rows = list(csv.DictReader(io.StringIO(original.decode())))
                assert Counter((r["text"], r["category"]) for r in original_rows) == Counter(
                    (r["text"], options[r["label"]]) for r in records
                )
                assert all(r["label_text"] == options[r["label"]] for r in records)
                receipt["original_csv_sha256"] = hashlib.sha256(original).hexdigest()
            receipts.append(receipt)
            start = len(rows)
            rows.extend(
                {
                    "source_row": start + index,
                    "benchmark": benchmark,
                    "split": split,
                    "record": record,
                    "source": receipt,
                }
                for index, record in enumerate(records)
            )
        source = directory / "source.jsonl"
        source.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows))
        manifest[benchmark] = {
            "repo": repo,
            "revision": revision,
            "files": receipts,
            "source": str(source),
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        }
        print(benchmark, len(rows), flush=True)
    (destination / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (destination / "banking77-options.json").write_text(json.dumps(options, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    main(parser.parse_args().destination)
