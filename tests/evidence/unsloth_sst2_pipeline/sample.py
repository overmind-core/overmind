import hashlib
import json
import sys
from pathlib import Path

with open(sys.argv[1]) as source, open(sys.argv[3]) as config:
    rows = [json.loads(line) for line in source]
    parameters = json.load(config)
blocked = (
    set(json.loads(Path(__file__).with_name("final-hashes.json").read_text()))
    if parameters["exclude_final"]
    else set()
)
groups = sorted(
    {row["content_group"] for row in rows} - blocked,
    key=lambda group: hashlib.sha256(f"{parameters['seed']}:{group}".encode()).hexdigest(),
)
selected = set(groups[: parameters["max_groups"]] if parameters["max_groups"] else groups)
with open(sys.argv[2], "w") as output:
    for row in rows:
        if row["content_group"] in selected:
            row["sample_scope"] = (
                "Deterministic content-group sample; duplicate observations stay together; official final-benchmark content excluded from training when requested."
            )
            output.write(json.dumps(row) + "\n")
