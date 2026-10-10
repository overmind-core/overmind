import json
import sys
from pathlib import Path

banking_options = json.loads((Path(__file__).parent / "banking77-options.json").read_text())
with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        record, benchmark = row["record"], row["benchmark"]
        if benchmark == "boolq":
            request = {
                "state": record["passage"],
                "question": record["question"],
                "kind": "noul",
                "options": ["No", "Yes"],
            }
        elif benchmark == "banking77":
            request = {
                "state": record["text"],
                "question": "Which banking customer-service intent does this query express?",
                "kind": "choice",
                "options": banking_options,
            }
        else:
            request = {
                "state": record["text"],
                "question": "What is the sentiment of this movie review?",
                "kind": "choice",
                "options": ["very negative", "negative", "neutral", "positive", "very positive"],
            }
        target = [int(index == row["label"]) for index in range(len(request["options"]))]
        row["decision"] = {
            **request,
            "target_probabilities": target,
            "target_semantics": "categorical_gold",
            "target_provenance": {
                "dataset": row["source"]["repo"],
                "revision": row["source"]["revision"],
                "split": row["split"],
                "field": "answer" if benchmark == "boolq" else "label",
                "meaning": "Published benchmark annotation; no generated or revised labels.",
            },
        }
        row["input"] = {"decision": request}
        row["expected_output"] = {"probabilities": target}
        row["group"] = row["group_id"]
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
