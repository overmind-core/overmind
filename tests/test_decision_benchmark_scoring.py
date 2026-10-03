import json
import math

import pytest

from modal_shared.decision_inference import input_digest
from modal_shared.training_data import file_digest
from scripts.score_decision_benchmarks import compare_suite, score_suite


def fixture(tmp_path):
    suite = tmp_path / "suite"
    suite.mkdir()
    request = {"state": "evidence", "question": "Choose", "kind": "choice", "options": ["a", "b"]}
    inputs, references, predictions = [], [], []
    for i, (q, p) in enumerate(
        [([1, 0], [0.8, 0.2]), ([0, 1], [0.7, 0.3]), ([0.2, 0.8], [0.4, 0.6])]
    ):
        identity = {"key": str(i), "input_sha256": input_digest(request)}
        inputs.append({**identity, "decision": request})
        references.append(
            {
                **identity,
                "benchmark": "fixture",
                "group": str(i),
                "query": "q",
                "kind": "choice",
                "reference": {"probabilities": q},
                "metadata": {"language": "en"},
            }
        )
        predictions.append(
            {
                **identity,
                "kind": "choice",
                "model_identity": "model-sha",
                "probabilities": p,
                "log_probabilities": [math.log(x) for x in p],
            }
        )
    for name, rows in [
        ("inputs.jsonl", inputs),
        ("references.jsonl", references),
        ("failures.jsonl", []),
    ]:
        (suite / name).write_text("".join(json.dumps(r) + "\n" for r in rows))
    (suite / "manifest.json").write_text(
        json.dumps(
            {
                "decisions": 3,
                "files": {
                    name: file_digest(suite / name)
                    for name in ["inputs.jsonl", "references.jsonl", "failures.jsonl"]
                },
            }
        )
    )
    prediction = tmp_path / "predictions.jsonl"
    prediction.write_text("".join(json.dumps(r) + "\n" for r in predictions))
    return suite, prediction, predictions


def test_scoring_preserves_soft_labels_and_reports_hard_label_denominator(tmp_path):
    suite, prediction, _ = fixture(tmp_path)
    result = score_suite(suite, prediction, tmp_path / "result", bootstrap_samples=40)
    card = result["benchmarks"]["fixture"]
    assert card["scored"] == 3 and card["missing_predictions"] == 0
    assert card["metrics"]["accuracy"]["mean"] == 0.5
    assert card["metrics"]["accuracy"]["decisions"] == 2
    assert card["metrics"]["top1_reference_mass"]["mean"] == pytest.approx(0.6)
    assert card["metrics"]["cross_entropy"]["mean"] == pytest.approx(
        (-math.log(0.8) - math.log(0.3) - 0.2 * math.log(0.4) - 0.8 * math.log(0.6)) / 3
    )
    assert card["macro_f1"] == pytest.approx(1 / 3)
    assert card["metrics"]["cross_entropy"]["groups"] == 3


def test_missing_and_invalid_predictions_remain_in_coverage(tmp_path):
    suite, prediction, rows = fixture(tmp_path)
    rows[0]["probabilities"] = [1, 1]
    prediction.write_text(json.dumps(rows[0]) + "\n" + json.dumps(rows[2]) + "\n")
    result = score_suite(suite, prediction, tmp_path / "result", bootstrap_samples=10)
    card = result["benchmarks"]["fixture"]
    assert card["expected"] == 3 and card["scored"] == 1
    assert card["invalid_predictions"] == 1 and card["missing_predictions"] == 1
    assert card["metrics"]["cross_entropy"]["interval_95"] is None


@pytest.mark.parametrize("change", ["unknown", "hash", "duplicate", "model", "source"])
def test_mixed_or_changed_prediction_inputs_are_rejected(tmp_path, change):
    suite, prediction, rows = fixture(tmp_path)
    if change == "unknown":
        rows[0]["key"] = "foreign"
    if change == "hash":
        rows[0]["input_sha256"] = "changed"
    if change == "duplicate":
        rows.append(rows[0])
    if change == "model":
        rows[0]["model_identity"] = "different-model"
    if change == "source":
        (suite / "inputs.jsonl").write_text("tampered")
    prediction.write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(ValueError):
        score_suite(suite, prediction, tmp_path / "result", bootstrap_samples=10)


def test_paired_comparison_matches_keys_and_keeps_correlated_queries_together(tmp_path):
    suite, baseline, rows = fixture(tmp_path)
    refs = [json.loads(line) for line in (suite / "references.jsonl").read_text().splitlines()]
    refs[1]["group"] = refs[0]["group"]
    (suite / "references.jsonl").write_text("".join(json.dumps(r) + "\n" for r in refs))
    manifest = json.loads((suite / "manifest.json").read_text())
    manifest["files"]["references.jsonl"] = file_digest(suite / "references.jsonl")
    (suite / "manifest.json").write_text(json.dumps(manifest))
    candidate = tmp_path / "candidate.jsonl"
    for row, p in zip(rows, [[0.3, 0.7], [0.2, 0.8], [0.2, 0.8]], strict=True):
        row.update(
            model_identity="candidate-sha",
            probabilities=p,
            log_probabilities=[math.log(x) for x in p],
        )
    candidate.write_text("".join(json.dumps(r) + "\n" for r in reversed(rows)))
    result = compare_suite(suite, baseline, candidate, tmp_path / "paired", bootstrap_samples=40)
    card = result["benchmarks"]["fixture"]
    assert card["paired_decisions"] == 3
    accuracy = card["metrics"]["accuracy"]
    assert accuracy["candidate_minus_baseline"] == 0
    assert accuracy["groups"] == 1 and accuracy["interval_95"] is None
    assert accuracy["decisions"] == 2
    ce = card["metrics"]["cross_entropy"]
    assert ce["groups"] == 2 and ce["direction"] == "lower_is_better"
    expected = (
        math.log(0.8 / 0.3)
        + math.log(0.3 / 0.8)
        + 0.2 * math.log(0.4 / 0.2)
        + 0.8 * math.log(0.6 / 0.8)
    ) / 3
    assert ce["candidate_minus_baseline"] == pytest.approx(expected)
    assert (tmp_path / "paired/baseline/scored.jsonl").is_file()
    assert (tmp_path / "paired/candidate/scored.jsonl").is_file()


def test_paired_comparison_exposes_unmatched_valid_predictions(tmp_path):
    suite, baseline, rows = fixture(tmp_path)
    baseline.write_text("".join(json.dumps(r) + "\n" for r in rows[:2]))
    rows[1]["probabilities"] = [1, 1]
    candidate = tmp_path / "candidate.jsonl"
    candidate.write_text("".join(json.dumps(r) + "\n" for r in rows))
    result = compare_suite(suite, baseline, candidate, tmp_path / "paired", bootstrap_samples=10)
    card = result["benchmarks"]["fixture"]
    assert card["paired_decisions"] == 1
    assert card["baseline_only"] == 1 and card["candidate_only"] == 1
    assert result["baseline"]["benchmarks"]["fixture"]["missing_predictions"] == 1
    assert result["candidate"]["benchmarks"]["fixture"]["invalid_predictions"] == 1


def test_paired_comparison_rejects_changed_candidate_input_without_publishing(tmp_path):
    suite, baseline, rows = fixture(tmp_path)
    rows[0]["input_sha256"] = "different"
    candidate = tmp_path / "candidate.jsonl"
    candidate.write_text("".join(json.dumps(r) + "\n" for r in rows))
    with pytest.raises(ValueError):
        compare_suite(suite, baseline, candidate, tmp_path / "paired", bootstrap_samples=10)
    assert not (tmp_path / "paired").exists()


@pytest.mark.parametrize("all_in_scope", [False, True])
def test_clinc_detection_respects_tied_scores_and_missing_target_classes(tmp_path, all_in_scope):
    suite, prediction, predictions = fixture(tmp_path)
    inputs = [json.loads(line) for line in (suite / "inputs.jsonl").read_text().splitlines()]
    refs = [json.loads(line) for line in (suite / "references.jsonl").read_text().splitlines()]
    for i, (request, ref, row) in enumerate(zip(inputs, refs, predictions, strict=True)):
        request["decision"]["options"] = ["in_scope", "oos"]
        digest = input_digest(request["decision"])
        request["input_sha256"] = ref["input_sha256"] = row["input_sha256"] = digest
        ref["benchmark"] = "CLINC150"
        out_of_scope = i != 1 and not all_in_scope
        ref["reference"] = {"probabilities": [int(not out_of_scope), int(out_of_scope)]}
        p = [0.1, 0.9] if i < 2 else [0.8, 0.2]
        row.update(probabilities=p, log_probabilities=[math.log(x) for x in p])
    manifest = json.loads((suite / "manifest.json").read_text())
    for name, rows in (("inputs.jsonl", inputs), ("references.jsonl", refs)):
        (suite / name).write_text("".join(json.dumps(row) + "\n" for row in rows))
        manifest["files"][name] = file_digest(suite / name)
    (suite / "manifest.json").write_text(json.dumps(manifest))
    prediction.write_text("".join(json.dumps(row) + "\n" for row in predictions))
    result = score_suite(suite, prediction, tmp_path / "result", bootstrap_samples=10)
    detection = result["benchmarks"]["CLINC150"]["out_of_scope_detection"]
    if all_in_scope:
        assert detection["positive_decisions"] == 0 and detection["negative_decisions"] == 3
        assert detection["auroc"] is None and detection["fpr_at_95_tpr"] is None
    else:
        assert detection["positive_decisions"] == 2 and detection["negative_decisions"] == 1
        assert detection["auroc"] == pytest.approx(0.25)
        assert detection["average_precision"] == pytest.approx(7 / 12)
        assert detection["fpr_at_95_tpr"] == 1
