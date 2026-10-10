import json
import sys

with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        if not row["exclusion_reason"]:
            output.write(json.dumps(row, ensure_ascii=False) + "\n")
