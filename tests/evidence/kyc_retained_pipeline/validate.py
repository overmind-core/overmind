import json
import sys

with (
    open(sys.argv[1], encoding="utf-8") as source,
    open(sys.argv[2], "w", encoding="utf-8") as output,
):
    for position, line in enumerate(source):
        row = json.loads(line)
        identity = row.get("source_row")
        if type(identity) is not int or identity < 0:
            raise ValueError(f"Row {position}: source_row must be a nonnegative integer")
        for field in ("tokens", "kyc_risk_bucket"):
            value = row.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Row {position}: {field} must be nonempty text")
        # Validation never trims evidence, interprets text as instructions or invents a label.
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
