import json
import math
import sys

with open(sys.argv[3]) as parameters:
    required = json.load(parameters)["required_semantics"]
with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        target = row["target"]
        assert row["kind"] in {"choice", "noul", "score"}
        assert row["question"].strip()
        assert all(type(p) in (float, int) and math.isfinite(p) and 0 <= p <= 1 for p in target)
        if row["kind"] == "noul":
            assert row["options"] == [] and len(target) == 1
        else:
            assert 2 <= len(row["options"]) <= 255
            assert len(set(row["options"])) == len(row["options"]) == len(target)
            assert math.isclose(math.fsum(target), 1, rel_tol=0, abs_tol=1e-6)
        semantics = row.get("target_semantics")
        if required and semantics != required:
            raise ValueError(
                f"Row {row['source_row']}: required target meaning lacks source evidence"
            )
        row["review_flags"] = [] if semantics else ["target_interpretation_unverified"]
        row["target_evidence"] = {
            "field": "target",
            "scope": "Supplied numerical distribution; its annotation or posterior interpretation is unverified.",
            "source": row["source"],
        }
        output.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
