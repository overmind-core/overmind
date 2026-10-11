import hashlib
import json
import sys
from collections import Counter

with open(sys.argv[1]) as source:
    rows = [json.loads(line) for line in source]
parents = {}


def root(value):
    parents.setdefault(value, value)
    while parents[value] != value:
        parents[value] = parents[parents[value]]
        value = parents[value]
    return value


for row in rows:
    assert row["judgement"] in {"positive", "negative"}
    identifiers = []
    for side in ("left", "right"):
        entity = row[side]
        assert isinstance(entity, dict) and isinstance(entity["properties"], dict)
        identifiers.extend([entity["id"], *entity.get("referents", [])])
    for value in identifiers[1:]:
        left, right = root(identifiers[0]), root(value)
        parents[max(left, right)] = min(left, right)
    row["pair_identity"] = hashlib.sha256(
        json.dumps(sorted([row["left"]["id"], row["right"]["id"]])).encode()
    ).hexdigest()
counts = Counter(row["pair_identity"] for row in rows)
with open(sys.argv[2], "w") as output:
    for row in rows:
        row["entity_group"] = hashlib.sha256(root(row["left"]["id"]).encode()).hexdigest()
        row["review_flags"] = []
        if counts[row["pair_identity"]] > 1:
            row["review_flags"].append("repeated_pair_observation")
        if row["judgement"] == "negative" and row["left"]["caption"] == row["right"]["caption"]:
            row["review_flags"].append("same_caption_negative")
        output.write(json.dumps(row, ensure_ascii=False) + "\n")
