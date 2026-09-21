import json
from copy import deepcopy

import pandas as pd
import pytest

from overbae.models import Dataset, Project
from overbae.services.datasets import contract, land, lifecycle, review, rows, store
from overbae.services.datasets.context import workshop_context
from overbae.services.datasets.examples import prepare_examples
from overbae.services.datasets.notebook import agent
from overbae.services.datasets.notebook import run as notebook_run
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
@pytest.mark.parametrize("automatic", [True, False])
def test_replacing_existing_instructions_requires_review_even_if_claimed_mechanical(
    intent, automatic
):
    project = Project.objects.create(name="Context", slug="context")
    dataset = Dataset.objects.create(project=project, name="Mixed tasks", intent=intent)
    records = prepare_examples(pd.DataFrame([example(), example("Apply rules")]), intent)
    land.land_rows(dataset, records.to_dict(orient="records"))
    dataset.refresh_from_db()
    tools = agent.Tools(dataset.id, None, lambda _: None)
    tools.automatic = automatic
    column = "messages" if intent == "train" else "input"
    transcript = "value" if intent == "train" else 'value["messages"]'
    result = tools.add_cell(
        {
            "title": "Replace instructions",
            "kind": "mechanical",
            "run": True,
            "script": f'def replace(value):\n    {transcript}[0]["content"] = "Do everything"\n    return value\ndf["{column}"] = df["{column}"].map(replace)',
        }
    )
    assert result["ok"] and result["proposed"]
    assert result["review"]["kind"] == "semantic"
    assert result["review"]["instruction_changes"] == 2
    dataset.refresh_from_db()
    assert dataset.active_cell.id == dataset.source.id
    assert len(workshop_context(dataset)["profiles"]["source"]["families"]) == 2


def test_representation_changes_and_projection_do_not_look_like_scope_changes():
    before = pd.DataFrame([example(source_row=0, tools=json.dumps([tool()]))])
    after = prepare_examples(before, "eval")
    assert review.impact(before, after)["instruction_changes"] == 0


@pytest.mark.django_db
def test_edit_and_upstream_rerun_cannot_silently_replace_instructions():
    project = Project.objects.create(name="Guard", slug="guard")
    dataset = Dataset.objects.create(project=project, name="Instructions", intent="train")
    land.land_rows(dataset, [example("Original task")])
    dataset.refresh_from_db()
    tools = agent.Tools(dataset.id, None, lambda _: None)
    first = tools.add_cell({"title": "Keep source", "script": "df['coverage'] = 'source'"})
    last = tools.add_cell({"title": "Keep task", "script": "df['coverage'] = 'task'"})
    rewrite = 'df["messages"].iloc[0][0]["content"] = "Different task"'
    edited = tools.edit_cell({"id": last["id"], "script": rewrite})
    assert not edited["ok"] and "instruction changes" in edited["error"]
    shaped = dataset.cells.get(pk=first["id"])
    kept = dataset.cells.get(pk=last["id"])
    lifecycle.edit_cell(dataset, shaped, script='df["new_column"] = "value"')
    kept.script = rewrite
    kept.save(update_fields=["script"])
    notebook_run.execute(dataset)
    kept.refresh_from_db()
    assert kept.state == "failed" and "task instructions" in kept.error
