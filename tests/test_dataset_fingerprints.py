import pytest

from overbae.services.datasets.partition import contamination_keys, content_key, preserve_lineage


@pytest.mark.parametrize(
    "column",
    [
        "state",
        "question",
        "prompt",
        "instruction",
        "context",
        "evidence",
        "passage",
        "text",
        "options",
        "choices",
        "score",
        "label",
        "output",
    ],
)
def test_tabular_fingerprints_do_not_infer_feature_or_target_roles_from_column_names(column):
    first = {column: "shared", "measurement": 10}
    assert content_key(first) != content_key({**first, "measurement": 20})
    assert content_key(first) != content_key({**first, column: "different"})
    assert content_key(first) == content_key({**first, "source_row": 42})


def test_source_record_and_explicit_input_fingerprints_have_distinct_meanings():
    record = {"text": "shared", "measurement": 10}
    assert content_key(record) != content_key({"input": record})


def test_corrected_fingerprints_do_not_discard_retained_lineage_or_declared_groups():
    original = {
        "state": "active",
        "measurement": 10,
        "group_id": "customer-1",
        "_overmind_provenance": {
            "source_content_keys": ["historical-source-fingerprint"],
            "seed_content_keys": ["synthetic-seed-fingerprint"],
        },
    }
    transformed = {
        "input": "different representation",
        "_overmind_provenance": preserve_lineage(original),
    }
    assert contamination_keys(original) <= contamination_keys(transformed)


@pytest.mark.parametrize("shape", ["flat", "nested", "wrapped"])
def test_declared_decision_inputs_group_target_variants_across_wire_formats(shape):
    request = {
        "state": "same evidence",
        "question": "Choose",
        "kind": "choice",
        "options": ["A", "B"],
    }
    canonical = content_key({"decision": request})
    for target in ([1, 0], [0, 1]):
        if shape == "flat":
            row = {**request, "target": target, "source": str(target)}
        elif shape == "nested":
            row = {"decision": {**request, "target_probabilities": target, "weight": 2}}
        else:
            row = {"input": {"decision": request}, "expected_output": {"probabilities": target}}
        assert content_key(row) == canonical
