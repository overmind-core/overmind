import json
from copy import deepcopy

import pandas as pd
import pytest
from conftest import import_version

from overbae.models import Dataset, Project
from overbae.services.datasets import contract, land, review, rows, store
from overbae.services.datasets.context import workshop_context
from overbae.services.datasets.examples import prepare_examples
from overbae.services.datasets.profile import profile_records
from overbae.services.finetuning_validator import row_to_finetuning_line, validate_rows


def example(prompt="Extract fields", **extra):
    return {
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": '{"document":"Supplied evidence"}'},
            {"role": "assistant", "content": '{"field":"Supported value"}'},
        ],
        **extra,
    }


def tool():
    return {"type": "function", "function": {"name": "lookup", "parameters": {"type": "object"}}}


def test_profile_counts_late_task_families_and_never_calls_them_a_quality_audit():
    records = [example()] * 300 + [example("Apply rules")] * 300 + [example("Summarise")] * 30
    profile = profile_records(iter(records))
    assert profile["rows_scanned"] == 630
    assert [family["rows"] for family in profile["families"]] == [300, 300, 30]
    assert [family["example_row"] for family in profile["families"]] == [0, 300, 600]
    assert "not a semantic audit" in profile["scope"]
    assert not profile["families_truncated"]


def test_profile_separates_nested_task_labels_targets_and_tools_without_a_capability():
    records = [example(), example(), example(), example()]
    records[1]["messages"][1]["content"] = '{"mode":"rules","facts":[1]}'
    records[2]["messages"][-1]["content"] = '{"verdict":true}'
    records[3]["tools"] = json.dumps([tool()])
    profile = profile_records(records)
    assert len(profile["families"]) == 4
    assert profile["families"][1]["task_labels"] == {"mode": "rules"}
    assert profile["counts"]["json_encoded_tools"] == 1
    assert profile["counts"]["with_recorded_tool_results"] == 0


def test_profile_is_bounded_and_reports_unlisted_families():
    profile = profile_records(example("x" * 30_000 + str(i)) for i in range(50))
    assert profile["rows_scanned"] == 50
    assert profile["families_truncated"]
    assert sum(f["rows"] for f in profile["families"]) + profile["unlisted_family_rows"] == 50
    assert len(json.dumps(profile)) < 35_000


def test_family_selection_is_not_biased_by_sorted_source_order():
    records = [example(f"Task {i}") for i in range(100) for _ in range(i + 1)]
    forward = profile_records(records)
    backward = profile_records(reversed(records))
    assert {f["family"]: f["rows"] for f in forward["families"]} == {
        f["family"]: f["rows"] for f in backward["families"]
    }
    assert forward["unlisted_family_rows"] == backward["unlisted_family_rows"]


@pytest.mark.parametrize("intent", ["train", "eval"])
def test_encoded_wire_fields_are_normalised_without_modifying_text_or_source(intent, tmp_path):
    source = example(tools=json.dumps([tool()]))
    original = deepcopy(source)
    source["messages"] = json.dumps(source["messages"])
    prepared = prepare_examples(pd.DataFrame([source]), intent)
    path = tmp_path / "frame.parquet"
    store.write_frame(path, prepared)
    persisted = next(store.iter_rows(path))
    assert contract.measure(pd.DataFrame([persisted]))[intent]["ok"]
    product_row = rows.row_from_record(0, persisted)
    if intent == "train":
        exported = row_to_finetuning_line(product_row)
        assert exported["messages"] == original["messages"]
        assert exported["tools"] == [tool()]
    else:
        assert product_row.input["messages"] == original["messages"][:-1]
        assert product_row.input["tools"] == [tool()]
    assert isinstance(source["tools"], str)
    assert prepare_examples(prepared, intent).equals(prepared)


def test_existing_encoded_tools_survive_the_consumer_handoff(tmp_path):
    source = example(tools=json.dumps([tool()]))
    path = tmp_path / "existing.parquet"
    store.write_rows(path, [source])
    record = next(store.iter_rows(path))
    assert isinstance(record["tools"], str)
    assert validate_rows([record]).valid
    assert row_to_finetuning_line(rows.row_from_record(0, record))["tools"] == [tool()]


@pytest.mark.parametrize(
    "bad_tools",
    [
        "not JSON",
        {},
        ["lookup"],
        [{"type": "function"}],
        [{"type": "function", "function": {"name": "lookup", "parameters": {"type": "invalid"}}}],
    ],
)
def test_invalid_tools_fail_even_when_no_assistant_calls_them(bad_tools):
    record = example(tools=bad_tools)
    result = validate_rows([record])
    assert not result.valid and "tools" in result.errors[0]
    assert not contract.measure(pd.DataFrame([record]))["train"]["ok"]
    with pytest.raises(ValueError, match="tools"):
        row_to_finetuning_line(rows.row_from_record(0, record))
    evaluation = prepare_examples(pd.DataFrame([record]), "eval")
    assert not contract.measure(evaluation)["eval"]["ok"]


@pytest.mark.django_db
@pytest.mark.parametrize("intent", ["train", "eval"])
def test_replacing_existing_instructions_records_semantic_impact_and_preserves_source(intent):
    project = Project.objects.create(name="Context", slug="context")
    dataset = Dataset.objects.create(project=project, name="Mixed tasks", intent=intent)
    records = prepare_examples(pd.DataFrame([example(), example("Apply rules")]), intent)
    land.land_rows(dataset, records.to_dict(orient="records"))
    dataset.refresh_from_db()
    from overbae.services.datasets import paths

    prepared = list(store.iter_rows(paths.cell_path(dataset.pk, dataset.active_cell.pk)))
    for row in prepared:
        transcript = row["messages"] if intent == "train" else row["input"]["messages"]
        transcript[0]["content"] = "Do everything"
    output = import_version(dataset, prepared, name="Replace instructions")
    assert output.review["instruction_changes"] == 2
    dataset.refresh_from_db()
    assert dataset.active_cell.id != dataset.source.id
    assert len(workshop_context(dataset)["profiles"]["source"]["families"]) == 2


def test_representation_changes_and_projection_do_not_look_like_scope_changes():
    before = pd.DataFrame([example(source_row=0, tools=json.dumps([tool()]))])
    after = prepare_examples(before, "eval")
    assert review.impact(before, after)["instruction_changes"] == 0


def test_decision_profiles_count_raw_and_native_targets_and_source_families():
    raw = {
        "state": "evidence",
        "question": "Choose",
        "kind": "noul",
        "options": [],
        "target": [0.7],
        "source": "first",
    }
    native = {
        "source": "second",
        "decision": {
            "state": "evidence",
            "question": "Choose",
            "kind": "choice",
            "options": ["A", "B"],
            "target_probabilities": [0.2, 0.8],
        },
    }
    profile = profile_records([raw, native])
    assert profile["counts"]["without_target"] == 0
    assert len(profile["families"]) == 2
    assert {f["task_labels"]["source"] for f in profile["families"]} == {"first", "second"}
    assert all("state" in f["input_shape"] for f in profile["families"])


@pytest.mark.django_db
def test_workshop_context_uses_profile_measured_for_the_frozen_frame(monkeypatch):
    from overbae.services.datasets import context

    project = Project.objects.create(name="Cached profile", slug="cached-profile")
    dataset = Dataset.objects.create(project=project, name="Cached", intent="train")
    land.land_rows(dataset, [example(), example("Second task")])
    monkeypatch.setattr(
        context, "profile_records", lambda *a: pytest.fail("Re-scanned immutable frame")
    )
    profile = context.workshop_context(dataset)["profiles"]["source"]
    assert profile["rows_scanned"] == 2 and len(profile["families"]) == 2
    assert profile["fingerprint"] == dataset.source.fingerprint
