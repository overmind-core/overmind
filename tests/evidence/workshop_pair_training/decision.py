import json
import sys

with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        row["decision"] = {
            "state": json.dumps(row["model_evidence"], ensure_ascii=False, sort_keys=True),
            "question": "Which supplied pair-matching judgement applies to these two entity records?",
            "kind": "choice",
            "options": ["negative", "positive"],
            "target_probabilities": [1, 0] if row["judgement"] == "negative" else [0, 1],
            "target_semantics": "categorical_gold",
            "target_provenance": {
                "field": "judgement",
                "evidence": "Source contains one explicit categorical judgement per pair; labels and their order are preserved, not regenerated.",
                "meaning_limit": "Matching meaning is inferred from pair structure; this is not a KYC approval label or independently verified truth.",
            },
        }
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
