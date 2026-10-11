import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def read(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream]


def verify(source, directory):
    with source.open() as stream:
        original = json.load(stream)["pairs"]
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    sampled = read(directory / "workshop-pairs-training-sample.jsonl")
    roles = {
        role: read(directory / f"workshop-pairs-{role}.jsonl")
        for role in ("train", "development", "final")
    }
    groups, identities, observations = {}, {}, []
    for role, rows in roles.items():
        for row in rows:
            file = row["_overmind_provenance"]["file"]
            assert file["sha256"] == source_hash
            index = file["row"]
            for field in ("left", "right", "judgement"):
                assert row[field] == original[index][field], (role, index, field)
            observations.append(index)
            group = row["entity_group"]
            assert groups.setdefault(group, role) == role
            for side in ("left", "right"):
                for identity in [row[side]["id"], *row[side].get("referents", [])]:
                    assert identities.setdefault(identity, role) == role
                assert set(row["model_evidence"][side]) == {"caption", "schema", "properties"}
            decision = row["decision"]
            assert json.loads(decision["state"]) == row["model_evidence"]
            assert decision["options"] == ["negative", "positive"]
            assert decision["target_probabilities"] == (
                [1.0, 0.0] if row["judgement"] == "negative" else [0.0, 1.0]
            )
            if role == "final":
                assert "target_probabilities" not in row["input"]["decision"]
                assert row["expected_output"]["probabilities"] == decision["target_probabilities"]
    assert Counter(observations) == Counter(
        row["_overmind_provenance"]["file"]["row"] for row in sampled
    )
    return {
        "passed": True,
        "source_sha256": source_hash,
        "source_rows": len(original),
        "sample_rows": len(sampled),
        "counts": {role: len(rows) for role, rows in roles.items()},
        "groups": len(groups),
        "cross_role_entity_overlap": 0,
        "supplied_labels_and_records_preserved": True,
        "limitations": [
            "Nested domain properties remain unchanged and can contain relationship identifiers.",
            "The sample tests execution mechanics, not production matching quality or label truth.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.source, args.directory), indent=2))
