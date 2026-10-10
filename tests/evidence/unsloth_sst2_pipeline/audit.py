import hashlib
import json
import sys

with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        if type(row.get("label")) is not int or row["label"] not in (0, 1):
            raise ValueError("SST-2 requires the published 0/1 sentiment label")
        if not isinstance(row.get("sentence"), str) or not row["sentence"].strip():
            raise ValueError("SST-2 sentence is missing")
        normalized = " ".join(row["sentence"].casefold().split())
        row["content_group"] = hashlib.sha256(normalized.encode()).hexdigest()
        output.write(json.dumps(row) + "\n")
