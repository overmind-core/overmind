import json
import sys

with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        row["decision"] = {
            "state": json.dumps(row["model_evidence"], ensure_ascii=False, sort_keys=True),
            "question": "Did this passenger survive the Titanic sinking?",
            "kind": "noul",
            "options": ["No", "Yes"],
            "target_probabilities": [1 - row["Survived"], row["Survived"]],
            "target_semantics": "categorical_gold",
            "target_provenance": {
                "field": "Survived",
                "meaning": "Recorded passenger outcome; 0 did not survive, 1 survived.",
            },
        }
        output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
