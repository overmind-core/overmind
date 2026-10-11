import json
import sys

with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        row["model_evidence"] = {
            side: {key: row[side][key] for key in ("caption", "schema", "properties")}
            for side in ("left", "right")
        }
        row["input_policy"] = (
            "caption, schema and properties only; top-level identity links, source datasets, timestamps and target flags are not model inputs"
        )
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
