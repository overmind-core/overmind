import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

with open(sys.argv[1]) as source:
    rows = [json.loads(line) for line in source]
options = json.loads((Path(__file__).parent / "banking77-options.json").read_text())
groups = defaultdict(set)
for row in rows:
    record, benchmark = row["record"], row["benchmark"]
    if benchmark == "boolq":
        assert type(record["answer"]) is bool
        assert isinstance(record["question"], str) and record["question"].strip()
        text, label = record["passage"], int(record["answer"])
    else:
        text, label = record["text"], record["label"]
        labels = (
            options
            if benchmark == "banking77"
            else ["very negative", "negative", "neutral", "positive", "very positive"]
        )
        assert type(label) is int and 0 <= label < len(labels)
        assert record["label_text"] == labels[label]
    assert isinstance(text, str) and text.strip()
    row["label"] = label
    row["group_id"] = hashlib.sha256(" ".join(text.casefold().split()).encode()).hexdigest()
    groups[row["group_id"]].add(row["split"])

with open(sys.argv[2], "w") as output:
    for row in rows:
        benchmark, split = row["benchmark"], row["split"]
        final_split = "validation" if benchmark == "boolq" else "test"
        overlap, role = "", ""
        if split == final_split:
            role = "final"
        elif final_split in groups[row["group_id"]]:
            overlap = "group_overlaps_official_final"
        elif benchmark == "sst5" and split == "validation":
            role = "development"
        elif benchmark == "sst5" and "validation" in groups[row["group_id"]]:
            overlap = "group_overlaps_official_development"
        else:
            assert split == "train"
            fraction = (
                int(hashlib.sha256(f"73491:{row['group_id']}".encode()).hexdigest(), 16) / 2**256
            )
            role = (
                "calibration"
                if fraction < 0.1
                else "development"
                if fraction < 0.2 and benchmark != "sst5"
                else "train"
            )
        row["benchmark_role"] = role
        row["exclusion_reason"] = overlap
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
