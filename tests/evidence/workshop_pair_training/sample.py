import hashlib
import json
import sys

with open(sys.argv[1]) as source, open(sys.argv[3]) as parameters:
    rows = [json.loads(line) for line in source]
    options = json.load(parameters)
groups = sorted(
    {row["entity_group"] for row in rows},
    key=lambda group: hashlib.sha256(f"{options['seed']}:{group}".encode()).hexdigest(),
)
selected = set(groups[: options["sample_groups"]])
with open(sys.argv[2], "w") as output:
    for row in rows:
        if row["entity_group"] in selected:
            row["sample_scope"] = (
                "Deterministic group sample for a bounded end-to-end training test; full prepared data remains in the preceding cell."
            )
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
