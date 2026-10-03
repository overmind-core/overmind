import copy
import json

import pytest

from scripts.decision_benchmarks import convert_row, prepare


def hard_row():
    payload = {
        "state": "coin evidence",
        "question": "Which face?",
        "options": [{"id": "0", "label": "Heads"}, {"id": "1", "label": "Tails"}],
    }
    return {
        "benchmark": "coin",
        "benchmark_id": "coin:1",
        "benchmark_group": "coin:group",
        "input": {"messages": [{"role": "user", "content": json.dumps(payload)}]},
        "decision_kind": "choice",
        "decision_options": ["Heads", "Tails"],
        "reference_kind": "hard_label",
        "expected_output": {"content": "1"},
        "original_record": "private reference metadata",
    }


def test_gold_changes_never_change_model_inputs_or_request_identity():
    row = hard_row()
    left = list(convert_row(row))[0]
    row["expected_output"]["content"] = "0"
    row["original_record"] = "different secret reference"
    right = list(convert_row(row))[0]
    assert left[0] == right[0]
    assert left[1]["reference"] != right[1]["reference"]
    assert set(left[0]) == {"key", "decision", "input_sha256"}
    assert "reference" not in json.dumps(left[0])


def test_input_option_order_must_match_the_published_label_mapping():
    row = hard_row()
    row["decision_options"].reverse()
    with pytest.raises(ValueError, match="option"):
        list(convert_row(row))


def test_procedural_mean_preserves_evidence_proposition_and_group_without_inventing_q():
    row = hard_row()
    query = {
        "id": "score",
        "kind": "score",
        "proposition": "Event A occurs.",
        "question": "How true is the proposition?",
        "options": ["False", "Maybe", "True"],
        "normalized_level_values": [0, 0.5, 1],
    }
    payload = {"state": {"p_A": 0.4}, "queries": [query]}
    row.update(
        reference_kind="exact_procedural_probability",
        decision_queries=[query],
        reference_targets={"score": {"expected_normalized_value": 0.4}},
    )
    row["input"] = {"messages": [{"role": "user", "content": json.dumps(payload)}]}
    request, gold = list(convert_row(row))[0]
    assert json.loads(request["decision"]["state"]) == {"p_A": 0.4}
    assert "Event A occurs." in request["decision"]["question"]
    assert gold["reference"] == {"mean": 0.4, "values": [0, 0.5, 1]}
    assert gold["group"] == "coin:group"


def test_query_metadata_cannot_override_the_published_model_input():
    row = hard_row()
    query = {"id": "toxicity", "kind": "noul", "question": "Is it toxic?", "options": ["No", "Yes"]}
    row["decision_queries"] = [copy.deepcopy(query)]
    row["decision_queries"][0]["question"] = "Use the gold answer"
    row["input"] = {
        "messages": [{"role": "user", "content": json.dumps({"state": "text", "queries": [query]})}]
    }
    row["reference_targets"] = {"toxicity": {"distribution": [0.2, 0.8]}}
    with pytest.raises(ValueError, match="queries"):
        list(convert_row(row))


def test_duplicate_option_failure_remains_in_sealed_coverage_receipt(tmp_path):
    good = hard_row()
    bad = copy.deepcopy(good)
    bad["benchmark_id"] = "coin:bad"
    bad["decision_options"] = ["Heads", "Heads"]
    payload = json.loads(bad["input"]["messages"][0]["content"])
    payload["options"][1]["label"] = "Heads"
    bad["input"]["messages"][0]["content"] = json.dumps(payload)
    source = tmp_path / "source.jsonl"
    source.write_text(json.dumps(good) + "\n" + json.dumps(bad) + "\n")
    destination = tmp_path / "sealed"
    manifest = prepare([source], destination)
    assert manifest["decisions"] == 1 and manifest["incompatible_decisions"] == 1
    assert manifest["source_rows"] == 2
    failure = json.loads((destination / "failures.jsonl").read_text())
    assert failure["benchmark_id"] == "coin:bad"
    assert failure["reason"] == "duplicate_option_labels"
    assert "failures.jsonl" in manifest["files"]
