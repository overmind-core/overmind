import json
import sys

with open(sys.argv[3]) as stream:
    parameters = json.load(stream)
with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        request = {
            "state": row["sentence"],
            "question": "What is the sentiment of this movie review?",
            "kind": "choice",
            "options": ["negative", "positive"],
        }
        target = [1.0, 0.0] if row["label"] == 0 else [0.0, 1.0]
        row["decision"] = {
            **request,
            "target_probabilities": target,
            "target_semantics": "categorical_gold",
            "target_provenance": {
                "dataset": "stanfordnlp/sst2",
                "revision": parameters["revision"],
                "field": "label",
                "mapping": {"0": "negative", "1": "positive"},
                "meaning": "Published SST-2 sentiment annotation; no relabeling or generated labels.",
            },
        }
        row["input"] = {"decision": request}
        row["expected_output"] = {"probabilities": target}
        row["group"] = row["content_group"]
        output.write(json.dumps(row) + "\n")
