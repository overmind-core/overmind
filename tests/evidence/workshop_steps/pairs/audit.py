import collections
import sys

from common import canonical, digest, read, write


def audit(records):
    parents = {}

    def root(key):
        parents.setdefault(key, key)
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    identities = []
    keys = []
    for row in records:
        if row["judgement"] not in {"positive", "negative"}:
            raise ValueError("Unknown pair target; interpretation is required.")
        keys.append(digest(sorted([canonical(row["left"]), canonical(row["right"])])))
        entities = []
        for side in ("left", "right"):
            value = row[side]
            entities.extend(
                "id:" + item for item in [value.get("id"), *value.get("referents", [])] if item
            )
            entities.append("content:" + digest(value))
        for entity in entities[1:]:
            a, b = sorted((root(entities[0]), root(entity)))
            parents[b] = a
        identities.append(entities[0])
    counts = collections.Counter(keys)
    for row, identity, key in zip(records, identities, keys, strict=True):
        reasons = []
        if counts[key] > 1:
            reasons.append("duplicate_pair_observation")
        if row["judgement"] == "negative" and row["left"].get("caption") == row["right"].get(
            "caption"
        ):
            reasons.append("negative_shared_caption")
        yield {
            **row,
            "entity_group": digest(root(identity)),
            "pair_group": key,
            "review_reasons": reasons,
            "needs_review": bool(reasons),
        }


write(audit(read(sys.argv[1])), sys.argv[2])
