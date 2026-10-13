import pytest
from conftest import read_table

from overbae.models import Behaviour, BehaviourVersion, Capability, Dataset, Execution, Project
from overbae.services.datasets import executions, land, paths, review, store, workshop
from overbae.services.datasets.context import preparation_context
from overbae.services.datasets.lifecycle import DatasetError
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


def test_an_exclusion_records_its_impact_and_coverage_and_leaves_the_active_version():
    ds = dataset()
    cell = executions.run_now(
        ds, mode=Execution.Mode.CELL, parent=ds.source, script="df = df.iloc[1:]", title="Exclude"
    ).cell
    assert cell.impact["rows_removed"] == 1 and cell.impact["rows_after"] == 1
    assert cell.impact["coverage_before"]["label"]["minority"] == 1
    assert "minority" not in cell.impact["coverage_after"]["label"]
    ds.refresh_from_db()
    assert ds.active_cell == ds.source and ds.source.rows == 2


def test_impact_examples_follow_output_identity_not_row_position(tmp_path):
    ds = dataset()
    before = paths.cell_path(ds.id, ds.source.id)
    after = read_table(before).iloc[::-1].copy()
    after["expected_output"] = "abstain"
    store.write_frame(tmp_path / "after.parquet", after)
    report = review.impact(before, tmp_path / "after.parquet")
    assert [row[store.SOURCE_ROW] for row in report["input_examples"]] == [1, 0]
    assert [row[store.SOURCE_ROW] for row in report["output_examples"]] == [1, 0]
    assert report["input_examples"][0]["expected_output"] == "no"


@pytest.mark.django_db(transaction=True)
def test_synthetic_rows_append_to_the_parent_as_a_new_inactive_version():
    ds = dataset()
    rows = [{"input": "three", "expected_output": "yes", "label": "minority"}]
    run = workshop.add_synthetic_examples(ds, "v0", rows, "More minority cases")
    assert run.status == "ok" and run.cell.parent_id == ds.source.id
    frame = read_table(paths.cell_path(ds.id, run.cell.id))
    assert frame["input"].tolist() == ["one", "two", "three"]
    assert frame[store.SOURCE_ROW].tolist() == [0, 1, 2]
    provenance = frame[review.PROVENANCE_COLUMN].iloc[2]
    assert provenance["synthetic"] is True and provenance["instruction"] == "More minority cases"
    assert run.cell.impact["rows_added"] == 1
    ds.refresh_from_db()
    assert ds.active_cell == ds.source
    again = workshop.add_synthetic_examples(ds, "v0", rows, "More minority cases")
    assert again.cell == run.cell and again.execution is None


@pytest.mark.django_db(transaction=True)
def test_synthetic_rows_under_a_filtered_parent_are_new_rows_against_the_source():
    ds = dataset()
    first = executions.run_now(
        ds, mode=Execution.Mode.CELL, parent=ds.source, script="df = df.iloc[:1]", title="First"
    ).cell
    rows = [{"input": "three", "expected_output": "yes", "label": "minority"}]
    run = workshop.add_synthetic_examples(ds, first.label, rows, "More minority cases")
    source = paths.cell_path(ds.id, ds.source.id)
    report = review.impact(source, paths.cell_path(ds.id, run.cell.id))
    assert (report["rows_added"], report["rows_removed"]) == (1, 1)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize(
    ("rows", "instruction", "field", "message"),
    [
        (
            [{"input": "one", "expected_output": "yes", "label": "minority"}],
            "x",
            None,
            "duplicates",
        ),
        ([{"question": "q"}], "x", "rows", "does not have: question"),
        ([{}], "x", "rows", "Row 1 is empty."),
        ([{"input": "q", "source_row": 0}], "x", "rows", "the platform sets: source_row"),
        ([{"label": "minority"}], "x", None, "do not fit the eval intent"),
        ([], "x", "rows", "between 1 and 50"),
        ([{"input": "three"}], " ", "instruction", "instruction"),
    ],
)
def test_synthetic_rows_are_refused_in_words(rows, instruction, field, message):
    ds = dataset()
    if field is None:
        run = workshop.add_synthetic_examples(ds, None, rows, instruction)
        assert run.status == "failed" and message in run.execution.error
        return
    with pytest.raises(DatasetError, match=message) as refused:
        workshop.add_synthetic_examples(ds, None, rows, instruction)
    assert refused.value.field == field


def test_synthetic_rows_need_a_train_or_eval_intent():
    ds = dataset()
    Dataset.objects.filter(pk=ds.pk).update(intent="pending")
    ds.refresh_from_db()
    with pytest.raises(DatasetError, match="train or eval"):
        workshop.add_synthetic_examples(ds, None, [{"input": "x"}], "More")


def test_preparation_context_carries_behaviour_contracts():
    ds = dataset()
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
    with pytest.raises(ValueError, match="A split needs two"):
        split_rows(
            [source[0], {**source[0], "expected_output": "b"}], eval_percent=30, position="random"
        )


def test_contamination_matches_flat_and_nested_conversations():
    messages = [{"role": "user", "content": "one"}, {"role": "assistant", "content": "yes"}]
    assert content_key({"messages": messages}) == content_key({"input": {"messages": messages}})
    assert content_key({"messages": messages}) == content_key({"input": "one"})


def test_mechanical_repair_keeps_contamination_identity(tmp_path):
    ds = dataset()
    before = read_table(paths.cell_path(ds.id, ds.source.id))
    before["conversation_id"] = ["a", "b"]
    before["customer"] = ["c", "d"]
    after = before.drop(columns=["conversation_id", "customer"]).iloc[::-1].copy()
    store.write_frame(tmp_path / "before.parquet", before)
    store.write_frame(tmp_path / "after.parquet", after)
    review.finalize(
        tmp_path / "before.parquet",
        tmp_path / "after.parquet",
        tmp_path / "repaired.parquet",
        group_by=["customer"],
    )
    repaired = read_table(tmp_path / "repaired.parquet")
    assert repaired["conversation_id"].tolist() == ["b", "a"]
    assert repaired["customer"].tolist() == ["d", "c"]
