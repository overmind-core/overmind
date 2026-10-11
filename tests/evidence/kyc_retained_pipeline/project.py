import json
import sys

with (
    open(sys.argv[1], encoding="utf-8") as source,
    open(sys.argv[2], "w", encoding="utf-8") as output,
):
    for line in source:
        row = json.loads(line)
        # Reproduce the existing recipe, not a new interpretation of the KYC task.
        # Other labelled fields, IDs and risk scores must not become model input.
        projected = {
            "source_row": row["source_row"],
            "question": row["tokens"],
            "answer": row["kyc_risk_bucket"],
        }
        if "_overmind_provenance" in row:
            projected["_overmind_provenance"] = row["_overmind_provenance"]
        output.write(json.dumps(projected, ensure_ascii=False) + "\n")
