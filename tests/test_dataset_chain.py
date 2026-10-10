from __future__ import annotations

import uuid

import pytest
from conftest import EVAL_ROWS, TRAIN_ROWS, import_version, review_fixture

from overbae.models import Capability, Cell, Dataset, EvalRun, Project
from overbae.services.datasets import diff, land, lifecycle, paths, store, use

pytestmark = pytest.mark.django_db


def _project() -> Project:
    return Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")


def _landed(project: Project, rows, name="ds", intent="pending") -> Dataset:
    dataset = Dataset.objects.create(project=project, name=name, intent=intent)
    land.land_rows(dataset, rows)
    dataset.refresh_from_db()
    return dataset


ROWS = [
    {"question": "q1", "answer": "a1", "tag": "keep"},
    {"question": "", "answer": "orphan", "tag": "junk"},
    {"question": "q3", "answer": "a3", "tag": "keep"},
]
KEEP = "df = df[df['tag'] == 'keep']\n"
SHAPE = "df = df.rename(columns={'question': 'input', 'answer': 'expected_output'})\n"


def _source(tmp_path, rows=ROWS):
    path = tmp_path / "source.parquet"
    for i, row in enumerate(rows):
        row.setdefault("source_row", i)
    store.write_rows(path, rows)
    return path


def test_landing_writes_cell_zero_and_keeps_unspecified_intent_pending():
    dataset = _landed(_project(), [dict(r) for r in TRAIN_ROWS])
    source = dataset.source
    assert source is not None and source.position == 0 and source.state == "ok"
    assert source.rows == len(TRAIN_ROWS)
    assert dataset.intent == "pending"
    assert dataset.state == "idle"
    assert dataset.versions()[source.id] == "1.0"
    assert paths.cell_path(dataset.id, source.id).exists()


def test_landing_keeps_a_chosen_intent_and_ranks_capabilities():
    project = _project()
    capability = Capability.objects.create(project=project, name="KB", slug="kb")
    rows = [{**r, "capability_id": str(capability.id)} for r in EVAL_ROWS]
    dataset = _landed(project, rows, intent="train")
    assert dataset.intent == "train"
    assert dataset.capability_id == capability.id
    assert dataset.capability_rank[0]["capability_id"] == str(capability.id)
    assert dataset.capability_rank[0]["score"] == 1.0


def test_run_executes_queued_cells_in_order_and_numbers_versions():
    dataset = _landed(_project(), ROWS)
    keep = import_version(
        dataset,
        [dict(r, source_row=i) for i, r in enumerate(ROWS) if r["tag"] == "keep"],
        name="Keep",
    )
    shape = import_version(
        dataset,
        [
            dict(r, input=r["question"], expected_output=r["answer"])
            for r in store.iter_rows(paths.cell_path(dataset.pk, keep.pk))
        ],
        name="Shape",
    )
    dataset.refresh_from_db()
    keep.refresh_from_db()
    shape.refresh_from_db()
    assert dataset.state == "idle"
    assert (
        keep.state == "ok"
        and keep.rows == 2
        and keep.input_fingerprint == dataset.source.fingerprint
    )
    assert shape.state == "ok" and shape.input_fingerprint == keep.fingerprint
    assert dataset.versions() == {dataset.source.id: "1.0", keep.id: "1.1", shape.id: "1.2"}
    assert dataset.active_cell == shape
    assert shape.fits("eval") == (True, "")


def test_use_refuses_a_wrong_intent_and_a_failing_contract():
    dataset = _landed(_project(), ROWS, intent="eval")
    with pytest.raises(lifecycle.DatasetError, match="needs train"):
        use.use(dataset, "train")
    with pytest.raises(lifecycle.DatasetError, match="no input column"):
        use.use(dataset, "eval")


def test_use_marks_the_cell_and_starts_a_new_major(django_assert_num_queries):
    dataset = _landed(_project(), ROWS, intent="eval")
    keep = import_version(
        dataset,
        [dict(r, source_row=i) for i, r in enumerate(ROWS) if r["tag"] == "keep"],
        name="Keep",
    )
    shape = import_version(
        dataset,
        [
            dict(r, input=r["question"], expected_output=r["answer"])
            for r in store.iter_rows(paths.cell_path(dataset.pk, keep.pk))
        ],
        name="Shape",
    )
    shape.refresh_from_db()
    review_fixture(dataset, shape)
    cell = use.use(dataset, "eval")
    assert cell == shape and cell.used_at is not None
    assert dataset.versions()[shape.id] == "2.0"

    later = import_version(dataset, list(store.iter_rows(paths.cell_path(dataset.pk, shape.pk))))
    assert dataset.versions()[later.id] == "2.1"
    # Everything the used cell reads is frozen with it.
    keep.refresh_from_db()
    assert keep.frozen and shape.frozen and not later.frozen
    with pytest.raises(lifecycle.DatasetError, match="fixed"):
        lifecycle.set_intent(dataset, "train")
    # A second use of the same cell changes nothing.
    assert use.use(dataset, "eval", cell=shape).used_at == cell.used_at
    assert dataset.versions()[shape.id] == "2.0"

    chain = dataset.chain
    with django_assert_num_queries(0):
        assert dataset.versions(chain=chain) == {
            chain[0].id: "1.0",
            keep.id: "1.1",
            shape.id: "2.0",
            later.id: "2.1",
        }
        assert dataset.versions(chain=[]) == {}


def test_a_used_cell_is_protected_and_blocks_deletion():
    project = _project()
    dataset = _landed(project, [dict(r) for r in EVAL_ROWS], intent="eval")
    review_fixture(dataset)
    cell = use.use(dataset, "eval")
    EvalRun.objects.create(project=project, name="r", dataset=dataset, cell=cell)
    assert "used by runs" in lifecycle.delete_blocked_reason(dataset)
    with pytest.raises(lifecycle.DatasetError):
        lifecycle.delete_dataset(dataset)
    assert Cell.objects.filter(pk=cell.pk).exists()


def test_diff_marks_added_and_changed_values_and_lists_removed_rows(tmp_path):
    before = tmp_path / "a.parquet"
    after = tmp_path / "b.parquet"
    store.write_rows(
        before,
        [
            {"source_row": 0, "x": "a", "y": 1},
            {"source_row": 1, "x": "b", "y": 2},
            {"source_row": 2, "x": "c", "y": 3},
        ],
    )
    store.write_rows(
        after,
        [
            {"source_row": 0, "x": "a", "y": 1},
            {"source_row": 2, "x": "C", "y": 3},
            {"source_row": None, "x": "new", "y": 9},
        ],
    )
    summary = diff.summary(before, after)
    assert summary["rows_removed"] == 1 and summary["rows_added"] == 1
    assert summary["cells_changed"] == 1 and summary["changed_columns"] == {"x": 1}
    marks = diff.marks(before, after, [0, 2])
    assert marks == {2: {"before": {"x": "c"}}}
    removed = diff.removed_rows(before, after)
    assert [r["source_row"] for r in removed] == [1]
    detail = diff.between(before, after)
    assert detail["changed_examples"][0]["x"] == {"before": "c", "after": "C"}
