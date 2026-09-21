import json
from copy import deepcopy
from types import SimpleNamespace

import pandas as pd
import pytest
from conftest import frozen_dataset

from overbae.models import Capability, Dataset, EvalRun, EvalVariant, Project
from overbae.services.datasets import alignment, contract, land, paths, review, rows, store, use
from overbae.services.datasets.examples import prepare_examples
from overbae.services.datasets.notebook import agent
from overbae.services.datasets.partition import contamination_keys, split_rows
from overbae.services.eval.context import snapshot_context
from overbae.services.eval.sampling import select_rows
from overbae.tasks.eval import _resolve_items, _sample_reference, _seed_from_datapoint


def example(index=1, mode="extraction"):
    return {
        "messages": [
            {"role": "system", "content": "Extract the evidence."},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "packet_id": f"case-{index}",
                        "mode": mode,
                        "documents": [{"text": f"Entity {index} registered in GB"}],
                    }
                ),
            },
            {"role": "assistant", "content": json.dumps({"entity": f"Entity {index}"})},
        ],
        "mode": mode,
    }


def test_eval_conversion_preserves_complete_request_and_is_idempotent():
    source = pd.DataFrame([example(), {**example(2), "expected_output": None}])
    prepared = prepare_examples(source, "eval")
    for index, record in enumerate(prepared.to_dict(orient="records")):
        original = source.iloc[index]["messages"]
        assert record["input"]["messages"] == original[:-1]
        assert record["expected_output"] == original[-1]["content"]
        seed, _, _ = _seed_from_datapoint(rows.row_from_record(index, record))
        assert seed == original[:-1]
    assert prepared.equals(prepare_examples(prepared, "eval"))
    restored = prepare_examples(prepared, "train")
    assert restored["messages"].tolist() == source["messages"].tolist()
    assert "input" not in restored and "expected_output" not in restored


def test_embedded_reference_is_removed_from_the_request():
    record = example()
    prepared = (
        prepare_examples(
            pd.DataFrame(
                [
                    {
                        "input": record["messages"],
                        "expected_output": record["messages"][-1]["content"],
                    }
                ]
            ),
            "eval",
        )
        .iloc[0]
        .to_dict()
    )
    assert prepared["input"]["messages"] == record["messages"][:-1]


def test_eval_conversion_keeps_tool_results_and_schemas():
    record = example()
    record["messages"][2:2] = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "read-1",
                    "type": "function",
                    "function": {"name": "read", "arguments": '{"packet_id":"case-1"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "read-1", "content": "Entity 1 registered in GB"},
    ]
    record["tools"] = [
        {
            "type": "function",
            "function": {
                "name": "read",
                "parameters": {"type": "object"},
            },
        }
    ]
    prepared = prepare_examples(pd.DataFrame([record]), "eval").iloc[0].to_dict()
    assert prepared["input"]["messages"] == record["messages"][:-1]
    assert prepared["input"]["tools"] == record["tools"]
    seed, _, _ = _seed_from_datapoint(rows.row_from_record(0, prepared))
    assert [turn["role"] for turn in seed] == ["system", "user", "assistant", "tool"]
    assert seed[-1]["content"] == "Entity 1 registered in GB"
    assert prepare_examples(pd.DataFrame([prepared]), "train").iloc[0]["tools"] == record["tools"]


def test_identifier_aliases_find_cross_format_contamination_without_using_answers():
    train = example()
    evaluation = {"input": {"onboarding_packet_id": "case-1"}, "expected_output": "answer"}
    assert contamination_keys(train) & contamination_keys(evaluation)
    other = {
        "input": {"onboarding_packet_id": "case-2"},
        "expected_output": {"packet_id": "case-1"},
    }
    assert not contamination_keys(train) & contamination_keys(other)


def test_model_and_application_targets_remain_separate():
    record = {**example(), "expected_output": {"application_packet": "assembled elsewhere"}}
    prepared = prepare_examples(pd.DataFrame([record]), "eval").iloc[0].to_dict()
    assert prepared["expected_output"] == record["expected_output"]
    assert prepared["model_expected_output"] == record["messages"][-1]["content"]
    item = {
        "expected": prepared["expected_output"],
        "model_expected": prepared["model_expected_output"],
    }
    assert _sample_reference(item, is_generate=True) == record["messages"][-1]["content"]
    assert _sample_reference(item, is_generate=False) == record["expected_output"]
    assert (
        prepare_examples(pd.DataFrame([prepared]), "train").iloc[0]["messages"]
        == record["messages"]
    )


def test_prepared_request_keeps_prior_assistant_context_on_repeat_preparation():
    record = example()
    record["messages"].insert(-1, {"role": "assistant", "content": "Prior context"})
    prepared = prepare_examples(pd.DataFrame([record]), "eval")
    assert prepare_examples(prepared, "eval").equals(prepared)
    seed, _, _ = _seed_from_datapoint(rows.row_from_record(0, prepared.iloc[0].to_dict()))
    assert seed[-1]["content"] == "Prior context"
    assert prepare_examples(prepared, "train").iloc[0]["messages"] == record["messages"]


def test_lineage_survives_projection_and_keeps_related_rows_in_one_split():
    original = pd.DataFrame([{**example(i), "source_row": i} for i in range(6)])
    shaped = pd.DataFrame(
        [{"source_row": i, "input": f"rephrased {i}", "expected_output": "yes"} for i in range(6)]
    )
    shaped = review.preserve_provenance(original, shaped)
    original_rows = original.to_dict(orient="records")
    shaped_rows = shaped.to_dict(orient="records")
    assert all(
        contamination_keys(a) & contamination_keys(b)
        for a, b in zip(original_rows, shaped_rows, strict=True)
    )
    training, evaluation, _ = split_rows(
        original_rows + shaped_rows, eval_percent=30, position="random"
    )
    train_keys = set().union(*(contamination_keys(row) for row in training))
    assert not any(train_keys & contamination_keys(row) for row in evaluation)


def test_identifier_only_inputs_warn_without_failing_the_format_contract():
    report = contract.measure(
        pd.DataFrame([{"input": {"onboarding_packet_id": "case-1"}, "expected_output": "entity"}])
    )
    assert report["eval"]["ok"]
    assert "without evidence" in report["eval"]["warnings"][0]


def test_projecting_domain_evidence_to_an_identifier_is_a_reviewed_change():
    before = pd.DataFrame(
        [{"source_row": 0, "input": {"case_id": "one", "documents": ["evidence"]}}]
    )
    after = pd.DataFrame([{"source_row": 0, "input": {"case_id": "one"}}])
    assert review.impact(before, after)["input_evidence_removed"] == 1


def test_model_transcript_is_not_validated_as_an_application_entry_point():
    capability = SimpleNamespace(
        improvement_metadata={
            "capability_card": {
                "system_prompt": "Extract the evidence.",
                "input_schema": {
                    "type": "object",
                    "required": ["onboarding_packet_id"],
                    "properties": {"onboarding_packet_id": {"type": "string"}},
                },
                "output_schema": {"type": "object", "required": ["entity"]},
            }
        }
    )
    prepared = prepare_examples(pd.DataFrame([example()]), "eval")
    assert alignment.capability_contract(capability, prepared, "eval")["ok"]
    capability.improvement_metadata["capability_card"]["system_prompt"] = (
        "Orchestrate the workflow."
    )
    assert not alignment.capability_contract(capability, prepared, "eval")["ok"]


@pytest.mark.django_db
def test_mechanical_tool_prepares_eval_without_llm_and_context_loss_requires_review():
    project = Project.objects.create(name="Consumer contract", slug="consumer-contract")
    dataset = Dataset.objects.create(project=project, name="Cases", intent="eval")
    land.land_rows(dataset, [example()])
    dataset.refresh_from_db()
    tools = agent.Tools(dataset.id, None, lambda _: None)
    tools.automatic = True
    result = tools.prepare_examples({})
    assert result["ok"] and not result.get("proposed"), result
    active = dataset.active_cell
    assert contract.measure(store.read_frame(paths.cell_path(dataset.id, active.id)))["eval"]["ok"]
    lost = tools.add_cell(
        {
            "title": "Identifier projection",
            "script": "df['input'] = [{'onboarding_packet_id': 'case-1'} for _ in range(len(df))]",
        }
    )
    assert lost["proposed"] and lost["review"]["input_evidence_removed"] == 1
    assert use.use(dataset, "eval").id == active.id


@pytest.mark.django_db
def test_sampling_covers_the_whole_dataset_and_matches_across_runs():
    project = Project.objects.create(name="Sampling", slug="sampling-contract")
    source = pd.DataFrame([example(i, f"worker-{i // 30}") for i in range(90)])
    dataset = frozen_dataset(project, prepare_examples(source, "eval").to_dict(orient="records"))
    selected = select_rows(dataset.active_cell, limit=12)
    assert len(selected) == 12
    assert {row.extra["mode"] for row in selected} == {"worker-0", "worker-1", "worker-2"}
    before = EvalRun.objects.create(
        project=project,
        dataset=dataset,
        cell=dataset.active_cell,
        max_items=12,
        data_source="dataset",
    )
    after = EvalRun.objects.create(
        project=project,
        dataset=dataset,
        cell=dataset.active_cell,
        max_items=12,
        data_source="dataset",
    )
    assert _resolve_items(before) == _resolve_items(after)
    assert len(select_rows(dataset.active_cell, limit=12, fraction=0.1)) == 9


@pytest.mark.django_db
def test_prompt_snapshot_does_not_follow_capability_edits():
    project = Project.objects.create(name="Context", slug="context-contract")
    capability = Capability.objects.create(
        project=project,
        name="Extraction",
        slug="extraction",
        improvement_metadata={"system_prompt": "Canonical instruction"},
    )
    dataset = frozen_dataset(
        project, [{"input": "Evidence", "expected_output": "Entity"}], capability=capability
    )
    run = EvalRun.objects.create(project=project, dataset=dataset, cell=dataset.active_cell)
    variant = EvalVariant.objects.create(run=run, mode="generate")
    snapshot_context(run, [variant])
    first = deepcopy(variant.params)
    capability.improvement_metadata = {"system_prompt": "Changed instruction"}
    capability.save()
    run.refresh_from_db()
    snapshot_context(run, [variant])
    assert (
        variant.params
        == first
        == {"execution_mode": "model", "system_prompt": "Canonical instruction"}
    )
