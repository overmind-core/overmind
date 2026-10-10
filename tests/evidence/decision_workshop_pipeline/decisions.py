import json
import sys

with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        options, target = row["options"], row["target"]
        if row["kind"] == "noul":
            options, target = ["No", "Yes"], [1 - target[0], target[0]]
        row["decision"] = {
            "state": row["state"],
            "question": row["question"],
            "kind": row["kind"],
            "options": options,
            "target_probabilities": target,
            **{
                key: row[key]
                for key in ("weight", "target_semantics", "target_provenance")
                if key in row
            },
        }
        output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
