import uuid
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from django.db import close_old_connections

from overbae.models import Behaviour, BehaviourVersion, Capability, Dataset, Project
from overbae.services.datasets import land, lifecycle, paths, review, rows, store, synthetic, use
from overbae.services.datasets.context import preparation_context
from overbae.services.datasets.notebook import agent, run
from overbae.services.datasets.partition import content_key, split_rows

pytestmark = pytest.mark.django_db


def dataset():
    project = Project.objects.create(name="Preparation", slug="preparation")
    ds = Dataset.objects.create(project=project, name="Seed", intent="eval")
    land.land_rows(
        ds,
        [
            {"input": "one", "expected_output": "yes", "label": "minority"},
            {"input": "two", "expected_output": "no", "label": "majority"},
        ],
    )
    ds.refresh_from_db()
    return ds


def test_semantic_exclusions_wait_with_coverage_and_consume_reviewed_output():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    result = tools.add_cell({"title": "Exclude", "script": "df = df.iloc[1:]", "run": True})
    assert result["proposed"]
    cell = ds.cells.get(pk=result["id"])
    assert ds.active_cell.rows == 2
    assert cell.review["rows_removed"] == 1
    assert cell.review["coverage_before"]["label"]["minority"] == 1
    assert "minority" not in cell.review["coverage_after"]["label"]
    lifecycle.accept_proposal(ds, cell)
    with patch(
        "overbae.services.datasets.notebook.runner.run",
        side_effect=AssertionError("must not rerun"),
    ):
        run.execute(ds)
    assert ds.active_cell.rows == 1


def test_initial_preparation_runs_exclusions_and_preserves_source_and_coverage():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    tools.automatic = True
    result = tools.add_cell(
        {"title": "Clean rows", "script": "df = df.iloc[1:]", "kind": "mechanical", "run": True}
    )
    assert result["ok"] and not result.get("proposed")
    assert ds.active_cell.rows == 1 and ds.source.rows == 2
    assert ds.active_cell.review["approval"] == "preparation"
    assert ds.active_cell.review["rows_removed"] == 1
    assert ds.active_cell.review["coverage_before"]["label"]["minority"] == 1
    assert store.read_frame(paths.cell_path(ds.id, ds.source.id)).shape[0] == 2


def test_initial_preparation_still_honours_proposals_and_guards_row_additions():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    tools.automatic = True
    proposed = tools.add_cell(
        {"title": "Optional change", "script": "df = df.iloc[1:]", "run": False}
    )
    assert proposed["proposed"]
    added = tools.add_cell({"title": "More rows", "script": "df = pd.concat([df, df])"})
    assert added["proposed"]
    assert ds.active_cell.rows == 2


def test_preparation_cells_recalculate_impact_when_an_earlier_cell_changes():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    tools.automatic = True
    first = tools.add_cell({"title": "Shape", "script": "df['input'] = df['input'].str.title()"})
    last = tools.add_cell({"title": "Clean", "script": "df = df.iloc[1:]", "kind": "mechanical"})
    shaped = ds.cells.get(pk=first["id"])
    cleaned = ds.cells.get(pk=last["id"])
    lifecycle.edit_cell(ds, shaped, script="df['input'] = df['input'].str.upper()")
    run.execute(ds)
    shaped.refresh_from_db()
    cleaned.refresh_from_db()
    assert cleaned.state == "ok" and cleaned.rows == 1
    assert cleaned.review["input_fingerprint"] == shaped.fingerprint
    assert cleaned.review["output_fingerprint"] == cleaned.fingerprint
    assert cleaned.review["rows_removed"] == 1
    assert cleaned.review["output_examples"][0]["input"] == "TWO"


def test_cell_creation_reuses_an_identical_preview_but_not_a_changed_input():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    script = "df['input'] = df['input'] + '!'"
    with patch.object(run, "try_script", wraps=run.try_script) as execute:
        tools.try_script({"script": script})
        result = tools.add_cell({"title": "Prepared rows", "script": script})
        assert result["ok"] and execute.call_count == 1
        tools.try_script({"script": script, "after": "1.0"})
        result = tools.add_cell({"title": "Current rows", "script": script})
        assert result["ok"] and execute.call_count == 3


def test_stale_proposal_refuses_acceptance():
    ds = dataset()
    result = agent.Tools(ds.id, None, lambda _: None).add_cell(
        {"title": "Exclude", "script": "df = df.iloc[1:]", "kind": "semantic"}
    )
    cell = ds.cells.get(pk=result["id"])
    ds.source.__class__.objects.filter(pk=ds.source.pk).update(fingerprint="changed")
    with pytest.raises(lifecycle.DatasetError, match="stale"):
        lifecycle.accept_proposal(ds, cell)


def test_preparation_reuses_a_preview_and_checks_unchanged_rows_once():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    with (
        patch.object(run, "try_script", wraps=run.try_script) as execute,
        patch.object(review, "same_frame", wraps=review.same_frame) as compare,
    ):
        tools.try_script({"script": "df = prepare_examples(df, intent='eval')"})
        first = tools.prepare_examples({})
        assert first["ok"] and execute.call_count == 1
        compare.reset_mock()
        repeated = tools.prepare_examples({})
        assert repeated["ok"] and repeated["unchanged"]
        assert repeated["id"] == first["id"]
        assert repeated["rows"] == 2
        assert execute.call_count == 2 and compare.call_count == 1


@pytest.mark.parametrize(
    "change, value",
    [
        ("rows_removed", 1),
        ("rows_added", 1),
        ("identity_preserved", False),
        ("input_evidence_removed", 1),
        ("instruction_changes", 1),
    ],
)
def test_preparation_only_waives_approval_for_exclusions(change, value):
    changes = dict(
        rows_removed=0,
        rows_added=0,
        identity_preserved=True,
        input_evidence_removed=0,
        instruction_changes=0,
    )
    assert not review.requires_approval(changes)
    changes[change] = value
    assert review.requires_approval(changes)
    assert review.requires_approval(changes, allow_exclusions=True) == (change != "rows_removed")


@pytest.mark.parametrize("initial", [True, False], ids=["initial", "follow_up"])
@pytest.mark.parametrize("approve", [True, False], ids=["approve", "deny"])
def test_judgement_waits_for_a_decision_without_blocking_the_current_version(initial, approve):
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    tools.automatic = initial
    source_id = ds.active_cell.id
    source_fingerprint = ds.active_cell.fingerprint
    result = tools.add_cell(
        {
            "title": "Abstain on ambiguity",
            "script": "df['expected_output'] = 'abstain'",
            "kind": "semantic",
            "run": True,
            "note": "Use abstention for 2 ambiguous labels instead of choosing a class.",
        }
    )
    assert result["ok"] and result["proposed"]
    cell = ds.cells.get(pk=result["id"])
    ds.refresh_from_db()
    assert ds.active_cell.id == source_id
    assert cell.review["input_examples"][0]["expected_output"] == "yes"
    assert cell.review["output_examples"][0]["expected_output"] == "abstain"
    assert cell.review["coverage_before"] == cell.review["coverage_after"]
    assert use.use(ds, intent="eval").id == source_id
    if approve:
        lifecycle.accept_proposal(ds, cell)
        with patch(
            "overbae.services.datasets.notebook.runner.run",
            side_effect=AssertionError("must consume the approved preview"),
        ):
            run.execute(ds)
        assert ds.active_cell.id == cell.id
        assert store.read_frame(paths.cell_path(ds.id, cell.id)).expected_output.tolist() == [
            "abstain",
            "abstain",
        ]
    else:
        lifecycle.remove_cell(ds, cell)
        ds.refresh_from_db()
        assert not ds.cells.filter(state="proposed").exists()
        assert ds.active_cell.id == source_id
        assert ds.active_cell.fingerprint == source_fingerprint


def test_proposal_input_examples_follow_output_identity_not_row_position():
    ds = dataset()
    before = store.read_frame(paths.cell_path(ds.id, ds.source.id))
    after = before.iloc[::-1].copy()
    after["expected_output"] = "abstain"
    report = review.impact(before, after)
    assert [row[store.SOURCE_ROW] for row in report["input_examples"]] == [1, 0]
    assert [row[store.SOURCE_ROW] for row in report["output_examples"]] == [1, 0]
    assert report["input_examples"][0]["expected_output"] == "no"


def test_synthetic_rows_are_active_with_seed_lineage_and_reject_duplicates_or_invented_seeds():
    ds = dataset()
    args = {"instruction": "Cover variants", "generation_id": str(uuid.uuid4()), "target_rows": 3}
    with pytest.raises(ValueError, match="seed_row"):
        synthetic.add(ds, ds.source, [{"seed_row": 999, "row": {"input": "x"}}], **args)
    existing = store.read_frame(paths.cell_path(ds.id, ds.source.id)).to_dict(orient="records")[0]
    with pytest.raises(ValueError, match="duplicates"):
        synthetic.add(ds, ds.source, [{"seed_row": 0, "row": existing}], **args)
    cell = synthetic.add(
        ds, ds.source, [{"seed_row": 0, "row": {"input": "new", "expected_output": "yes"}}], **args
    )
    assert cell.state == "ok" and ds.active_cell.rows == 3
    assert ds.source.rows == 2
    assert not ds.cells.filter(state="proposed").exists()
    run.execute(ds)
    frame = store.read_frame(paths.cell_path(ds.id, cell.id))
    provenance = frame.iloc[-1][review.PROVENANCE_COLUMN]
    assert provenance["seed_row"] == 0 and provenance["capability"] is None
    assert content_key(existing) in provenance["seed_content_keys"]
    assert rows.contamination(ds.active_cell, ds.source)["overlap_count"] == 3


@pytest.mark.parametrize("with_capability", [False, True])
def test_generation_locks_only_the_dataset_with_optional_capability(with_capability):
    ds = dataset()
    if with_capability:
        ds.capability = Capability.objects.create(project=ds.project, name="Task", slug="task")
        ds.save(update_fields=["capability"])
    # SQLite ignores row locks, so also assert the PostgreSQL lock scope.
    with patch.object(
        Dataset.objects, "select_for_update", wraps=Dataset.objects.select_for_update
    ) as lock:
        cell = synthetic.add(
            ds,
            ds.source,
            [{"seed_row": 0, "row": {"input": "new", "expected_output": "yes"}}],
            instruction="Cover variants",
            generation_id=str(uuid.uuid4()),
            target_rows=3,
        )
    lock.assert_called_once_with(of=("self",))
    assert cell.state == "ok" and cell.review["generated_rows"] == 1
    assert ds.active_cell.rows == 3
    assert cell.review["output_examples"][-1][review.PROVENANCE_COLUMN]["capability"] == (
        str(ds.capability_id) if with_capability else None
    )


def test_quality_review_is_tied_to_data_intent_and_capability_context():
    ds = dataset()
    cell = ds.source
    review.record_quality(
        ds,
        cell,
        [{"name": "Labels", "result": "unknown", "evidence": "Not verified"}],
        script="df = pd.DataFrame({'Labels': [None] * len(df)})",
    )
    assert review.readiness(ds, cell)["quality_reviewed"]
    assert not review.readiness(ds, cell)["quality_passed"]
    ds.intent = "train"
    assert not review.readiness(ds, cell)["quality_reviewed"]
    capability = Capability.objects.create(project=ds.project, name="Task", slug="task")
    behaviour = Behaviour.objects.create(project=ds.project, capability=capability, key="route")
    BehaviourVersion.objects.create(
        behaviour=behaviour, analyzed_sha="abc", contract={"terminal": "answer"}
    )
    assert preparation_context(capability)["behaviours"][0]["contract"] == {"terminal": "answer"}


def test_split_clusters_content_and_groups_and_reports_stratification():
    source = [
        {"input": f"q{i}", "expected_output": "a", "customer": i // 2, "label": i % 2}
        for i in range(20)
    ]
    train, evaluation, report = split_rows(
        source + [source[0]],
        eval_percent=30,
        position="random",
        group_by=["customer"],
        stratify_by="label",
    )
    assert len(train) == 14 and len(evaluation) == 6
    assert {r["customer"] for r in train}.isdisjoint({r["customer"] for r in evaluation})
    assert report["duplicates_removed"] == 1 and report["content_overlap"] == 0
    assert all(stratum["eval"] == 3 for stratum in report["strata"].values())
    with pytest.raises(ValueError, match="independent holdout"):
        split_rows(
            [source[0], {**source[0], "expected_output": "b"}], eval_percent=30, position="random"
        )


def test_contamination_matches_flat_and_nested_conversations():
    messages = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "yes"}]
    assert content_key({"messages": messages}) == content_key({"input": {"messages": messages}})
    assert content_key({"messages": messages}) == content_key({"input": "one"})


def test_automatic_preparation_cannot_generate_synthetic_examples():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    tools.automatic = True
    result = tools.add_synthetic_rows({})
    assert not result["ok"] and "user request" in result["error"]


def test_generation_batches_accumulate_in_one_active_version():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    plan = tools.seed_examples({"target_rows": 5, "instruction": "Cover new cases"})
    assert plan["remaining_rows"] == 3
    results = [
        tools.add_synthetic_rows(
            {
                "examples": [
                    {"seed_row": 0, "row": {"input": f"new {index}", "expected_output": "yes"}}
                ]
            }
        )
        for index in range(3)
    ]
    assert len({r["id"] for r in results}) == 1
    assert [r["remaining_rows"] for r in results] == [2, 1, 0]
    assert len(tools.touched) == 1
    generated = ds.cells.get(pk=results[0]["id"])
    assert generated.review["rows_before"] == 2
    assert generated.review["rows_after"] == 5
    assert generated.review["generated_rows"] == 3
    assert generated.state == "ok"
    assert not ds.cells.filter(state="proposed").exists()
    assert ds.active_cell.rows == 5
    assert [r["version"] for r in results] == ["1.1"] * 3
    frame = store.read_frame(paths.cell_path(ds.id, generated.id))
    assert frame.source_row.is_unique


def test_generation_retry_is_idempotent_and_cross_batch_duplicates_are_rejected():
    ds = dataset()
    args = {"instruction": "Variants", "generation_id": str(uuid.uuid4()), "target_rows": 5}
    examples = [{"seed_row": 0, "row": {"input": "new", "expected_output": "yes"}}]
    first = synthetic.add(ds, ds.source, examples, **args)
    again = synthetic.add(ds, ds.source, examples, **args)
    assert first.pk == again.pk
    assert again.review["generated_rows"] == 1
    with pytest.raises(ValueError, match="duplicates"):
        synthetic.add(ds, ds.source, [{**examples[0], "seed_row": 1}], **args)
    first.refresh_from_db()
    assert first.review["generated_rows"] == 1


@pytest.mark.parametrize("explicit_cell", [False, True])
def test_partial_generation_resumes_without_replacing_saved_rows(explicit_cell):
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    tools.seed_examples({"target_rows": 4, "instruction": "Variants"})
    first = tools.add_synthetic_rows(
        {"examples": [{"seed_row": 0, "row": {"input": "new", "expected_output": "yes"}}]}
    )
    resumed = agent.Tools(ds.id, None, lambda _: None)
    plan = resumed.seed_examples(
        {
            "target_rows": 4,
            "instruction": "Continue",
            **({"cell_id": first["id"]} if explicit_cell else {}),
        }
    )
    assert plan["generated_rows"] == 1 and plan["remaining_rows"] == 1
    assert resumed.progress["cell_id"] == first["id"]
    assert resumed.generation["instruction"] == "Variants"
    result = resumed.add_synthetic_rows(
        {"examples": [{"seed_row": 1, "row": {"input": "another", "expected_output": "no"}}]}
    )
    assert result["id"] == first["id"] and result["rows_after"] == 4
    assert ds.active_cell.rows == 4 and ds.cells.count() == 2


def test_generation_never_changes_a_consumed_version():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    tools.seed_examples({"target_rows": 4, "instruction": "Variants"})
    tools.add_synthetic_rows(
        {"examples": [{"seed_row": 0, "row": {"input": "new", "expected_output": "yes"}}]}
    )
    review.record_quality(
        ds,
        ds.active_cell,
        [
            {"name": name, "result": "pass", "evidence": "Controlled fixture.", "rows_checked": 3}
            for name in review.REQUIRED_CHECKS
        ],
        script="df = pd.DataFrame({name: [True] * len(df) for name in ('task_alignment', 'input_evidence', 'answer_support', 'output_schema')})",
    )
    with patch.object(
        Dataset.objects, "select_for_update", wraps=Dataset.objects.select_for_update
    ) as lock:
        consumed = use.use(ds, "eval")
    lock.assert_called_once_with(of=("self",))
    with pytest.raises(ValueError, match="used"):
        tools.add_synthetic_rows(
            {"examples": [{"seed_row": 1, "row": {"input": "another", "expected_output": "no"}}]}
        )
    assert ds.active_cell.rows == 3
    assert store.file_sha256(paths.cell_path(ds.id, consumed.id)) == consumed.fingerprint


def test_generation_refuses_to_append_after_another_transformation():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    tools.seed_examples({"target_rows": 4, "instruction": "Variants"})
    first = tools.add_synthetic_rows(
        {"examples": [{"seed_row": 0, "row": {"input": "new", "expected_output": "yes"}}]}
    )
    tools.add_cell({"title": "Shape", "script": "df['input'] = df['input'].str.title()"})
    resumed = agent.Tools(ds.id, None, lambda _: None)
    assert (
        resumed.seed_examples(
            {"cell_id": first["id"], "target_rows": 4, "instruction": "Continue"}
        )["ok"]
        is False
    )
    assert ds.cells.get(pk=first["id"]).rows == 3


def test_resuming_checks_source_and_target_before_generating():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    tools.seed_examples({"target_rows": 4, "instruction": "Variants"})
    first = tools.add_synthetic_rows(
        {"examples": [{"seed_row": 0, "row": {"input": "new", "expected_output": "yes"}}]}
    )
    resumed = agent.Tools(ds.id, None, lambda _: None)
    args = {"cell_id": first["id"], "target_rows": 5, "instruction": "Continue"}
    assert resumed.seed_examples(args)["ok"] is False
    ds.source.__class__.objects.filter(pk=ds.source.pk).update(fingerprint="changed")
    assert resumed.seed_examples({**args, "target_rows": 4})["ok"] is False
    assert resumed.generation is None
    assert ds.cells.get(pk=first["id"]).review["generated_rows"] == 1


def test_generation_rejects_overshoot_and_changed_input_without_losing_saved_rows():
    ds = dataset()
    args = {"instruction": "Variants", "generation_id": str(uuid.uuid4()), "target_rows": 3}
    first = synthetic.add(
        ds, ds.source, [{"seed_row": 0, "row": {"input": "new", "expected_output": "yes"}}], **args
    )
    with pytest.raises(ValueError, match="remain"):
        synthetic.add(
            ds,
            ds.source,
            [{"seed_row": 0, "row": {"input": "extra", "expected_output": "yes"}}],
            **args,
        )
    ds.source.__class__.objects.filter(pk=ds.source.pk).update(fingerprint="changed")
    with pytest.raises(ValueError, match="changed"):
        synthetic.add(
            ds,
            ds.source,
            [{"seed_row": 0, "row": {"input": "extra", "expected_output": "yes"}}],
            **args,
        )
    first.refresh_from_db()
    assert first.review["generated_rows"] == 1


def test_generation_seeds_preserve_full_system_prompts():
    ds = dataset()
    long_prompt = "Declared system prompt. " * 100
    frame = store.read_frame(paths.cell_path(ds.id, ds.source.id))
    frame["system_prompt"] = long_prompt
    store.write_frame(paths.cell_path(ds.id, ds.source.id), frame)
    tools = agent.Tools(ds.id, None, lambda _: None)
    result = tools.seed_examples({"target_rows": 3, "instruction": "Variants"})
    assert result["examples"][0]["row"]["system_prompt"] == long_prompt


@pytest.mark.django_db(transaction=True)
def test_concurrent_generation_batches_share_one_active_version():
    ds = dataset()
    tools = agent.Tools(ds.id, None, lambda _: None)
    tools.seed_examples({"target_rows": 5, "instruction": "Variants"})
    append_batch = tools.handlers()["add_synthetic_rows"]

    def append(index):
        try:
            return append_batch(
                {
                    "examples": [
                        {"seed_row": 0, "row": {"input": f"new {index}", "expected_output": "yes"}}
                    ]
                }
            )["id"]
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=3) as pool:
        ids = list(pool.map(append, range(3)))
    assert len(set(ids)) == 1
    proposal = ds.cells.get(pk=ids[0])
    assert proposal.review["generated_rows"] == 3
    assert store.read_frame(paths.cell_path(ds.id, proposal.id)).source_row.is_unique


def test_mechanical_repair_keeps_contamination_identity():
    ds = dataset()
    before = store.read_frame(paths.cell_path(ds.id, ds.source.id))
    before["conversation_id"] = ["a", "b"]
    before["customer"] = ["c", "d"]
    after = before.drop(columns=["conversation_id", "customer"]).iloc[::-1].copy()
    repaired = review.preserve_provenance(before, after, group_by=["customer"])
    assert repaired["conversation_id"].tolist() == ["b", "a"]
    assert repaired["customer"].tolist() == ["d", "c"]
