import json
import sys

with (
    open(sys.argv[1], encoding="utf-8") as source,
    open(sys.argv[2], "w", encoding="utf-8") as output,
):
    for line in source:
        row = json.loads(line)
        # Only the supplied risk-bucket label is supervised; no rationale is fabricated.
        row["messages"] = [
            {"role": "user", "content": row["question"]},
            {"role": "assistant", "content": row["answer"]},
        ]
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
