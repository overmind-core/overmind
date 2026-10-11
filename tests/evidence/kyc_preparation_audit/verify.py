import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path


def verify(original_path, landed_path, prepared_path):
    original_bytes = original_path.read_bytes()
    original = json.loads(original_bytes)["pairs"]
    landed = [json.loads(line) for line in landed_path.open()]
    prepared = [json.loads(line) for line in prepared_path.open()]
    assert len(original) == len(landed) == len(prepared) == 10000
    by_identity = {row["source_row"]: row for row in landed}
    assert len(by_identity) == len(landed)
    pairs = Counter(tuple(sorted((row["left"]["id"], row["right"]["id"]))) for row in original)
    entity_groups = {}
    flags = Counter()
    for source, raw, output in zip(landed, original, prepared, strict=True):
        assert {key: source[key] for key in raw} == raw
        assert output["source_row"] == source["source_row"]
        assert {key: output[key] for key in raw} == raw
        provenance = output["_overmind_provenance"]
        original_provenance = source["_overmind_provenance"]
        assert provenance["file"] == original_provenance["file"]
        assert set(original_provenance["source_content_keys"]) <= set(
            provenance["source_content_keys"]
        )
        assert provenance["source_group_keys"] == original_provenance["source_group_keys"]
        assert provenance["parents"] == [
            {
                "cell": "78ee5bb8-d5a8-4ef9-bc43-3365e0f23a70",
                "fingerprint": "6ac342f1edab8d9e58595e2c9e8b406fd643c0b1f06a2d88bc9c49f26bbeff39",
                "row": source["source_row"],
            }
        ]
        decision = output["decision"]
        model_input = json.loads(decision["state"])
        assert set(model_input) == {"left", "right"}
        for side in ("left", "right"):
            assert set(model_input[side]) == {"caption", "schema", "properties"}
            assert model_input[side] == {key: raw[side][key] for key in model_input[side]}
            for identity in [raw[side]["id"], *raw[side]["referents"]]:
                assert (
                    entity_groups.setdefault(identity, output["entity_group"])
                    == output["entity_group"]
                )
        assert decision["options"] == ["negative", "positive"]
        target = decision["target_probabilities"]
        assert target == ([1, 0] if raw["judgement"] == "negative" else [0, 1])
        expected_flags = []
        if pairs[tuple(sorted((raw["left"]["id"], raw["right"]["id"])))] > 1:
            expected_flags.append("repeated_pair_observation")
        if raw["judgement"] == "negative" and raw["left"]["caption"] == raw["right"]["caption"]:
            expected_flags.append("same_caption_negative")
        assert output["review_flags"] == expected_flags
        flags.update(expected_flags)
    return {
        "original_sha256": hashlib.sha256(original_bytes).hexdigest(),
        "prepared_sha256": hashlib.sha256(prepared_path.read_bytes()).hexdigest(),
        "original_to_landed_equal_rows": len(landed),
        "source_records_and_lineage_preserved": len(prepared),
        "source_label_mapping_verified": len(prepared),
        "inputs_without_top_level_identity_metadata": len(prepared),
        "entity_groups": len(set(entity_groups.values())),
        "identity_assignments_verified": len(entity_groups),
        "labels": dict(Counter(row["judgement"] for row in prepared)),
        "review_flags": dict(flags),
        "limitations": [
            "Checks verify source fidelity, not the truth of the supplied labels.",
            "Nested property identifiers remain; their generalisation effect is unmeasured.",
            "No held-out split or model training is performed.",
        ],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--original", type=Path, required=True)
    parser.add_argument("--landed", type=Path, required=True)
    parser.add_argument("--prepared", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(verify(args.original, args.landed, args.prepared), indent=2))
