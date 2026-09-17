"""The chain: landing, the runner, runs, edits, diff, versions and the use gate."""

from __future__ import annotations

import uuid

import pytest
from conftest import EVAL_ROWS, TRAIN_ROWS

from overbae.models import Capability, Cell, Dataset, EvalRun, Project
from overbae.services.datasets import diff, land, lifecycle, paths, store, use
from overbae.services.datasets.notebook import libraries, runner
from overbae.services.datasets.notebook import run as run_svc

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


def test_cell_reads_df_and_keeps_source_row_through_a_filter(tmp_path):
    result = runner.run(KEEP, _source(tmp_path), library_cache=tmp_path / "lib")
    assert result.error == ""
    assert list(result.frame["question"]) == ["q1", "q3"]
    assert list(result.frame["source_row"]) == [0, 2]


def test_cell_rebuilds_source_row_when_the_script_dropped_it(tmp_path):
    script = "df = df[['question']][df['question'] != '']\n"
    result = runner.run(script, _source(tmp_path), library_cache=tmp_path / "lib")
    assert result.error == ""
    assert list(result.frame["source_row"]) == [0, 2]


def test_cell_without_row_identity_reports_it(tmp_path):
    script = "df = df.groupby('tag').size().reset_index(name='n')\n"
    result = runner.run(script, _source(tmp_path), library_cache=tmp_path / "lib")
    assert result.error == ""
    assert "source_row" not in result.frame.columns


def test_cell_error_carries_the_traceback(tmp_path):
    result = runner.run("df = df['missing']\n", _source(tmp_path), library_cache=tmp_path / "lib")
    assert result.frame is None
    assert "missing" in result.error


def test_audit_reads_the_library_list(tmp_path):
    cache = tmp_path / "lib"
    allowed = libraries.allowed_imports(cache)
    assert "sklearn" in allowed and "re" in allowed
    assert runner.audit("import os\nos.system('x')\n", allowed) == ["system is not allowed"] or (
        "os" not in allowed
    )
    assert runner.audit("import nltk\n", allowed) == ["import nltk is not allowed"]
    assert runner.audit("open('x')\n", allowed) == ["open() is not allowed"]


def test_library_resolve_and_refusal():
    assert libraries.resolve("scikit-learn") == ("sklearn", "scikit-learn")
    assert libraries.resolve("ftfy") == ("ftfy", "ftfy")
    with pytest.raises(libraries.LibraryError, match="not on the list"):
        libraries.resolve("requests")


def test_landing_writes_cell_zero_and_proposes_the_intent():
    dataset = _landed(_project(), [dict(r) for r in TRAIN_ROWS])
    source = dataset.source
    assert source is not None and source.position == 0 and source.state == "ok"
    assert source.rows == len(TRAIN_ROWS)
    assert dataset.intent == "train"
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
    keep = lifecycle.add_cell(dataset, title="Keep", script=KEEP)
    shape = lifecycle.add_cell(dataset, title="Shape", script=SHAPE)
    run_svc.execute(dataset)
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


def test_an_unchanged_cell_is_not_run_again():
    dataset = _landed(_project(), ROWS)
    keep = lifecycle.add_cell(dataset, title="Keep", script=KEEP)
    run_svc.execute(dataset)
    keep.refresh_from_db()
    first = keep.updated_at
    events = list(run_svc.iter_execute(dataset))
    keep.refresh_from_db()
    assert keep.updated_at == first
    assert any(e["type"] == "cell_done" and e.get("cached") for e in events)


def test_editing_a_cell_queues_it_and_everything_after():
    dataset = _landed(_project(), ROWS)
    keep = lifecycle.add_cell(dataset, title="Keep", script=KEEP)
    shape = lifecycle.add_cell(dataset, title="Shape", script=SHAPE)
    run_svc.execute(dataset)
    lifecycle.edit_cell(dataset, keep, script="df = df[df['tag'] != 'nothing']\n")
    keep.refresh_from_db()
    shape.refresh_from_db()
    assert keep.state == "queued" and shape.state == "queued"
    run_svc.execute(dataset)
    keep.refresh_from_db()
    shape.refresh_from_db()
    assert keep.rows == 3 and shape.state == "ok"
    assert dataset.versions()[shape.id] == "1.2"


def test_a_failing_cell_stops_the_run_and_leaves_the_rest_queued():
    dataset = _landed(_project(), ROWS)
    bad = lifecycle.add_cell(dataset, title="Bad", script="df = df['missing']\n")
    after = lifecycle.add_cell(dataset, title="After", script=KEEP)
    run_svc.execute(dataset)
    dataset.refresh_from_db()
    bad.refresh_from_db()
    after.refresh_from_db()
    assert dataset.state == "error" and "Bad:" in dataset.error
    assert bad.state == "failed" and "missing" in bad.error
    assert after.state == "queued"
    assert dataset.active_cell == dataset.source


def test_a_proposal_has_no_version_until_accepted():
    dataset = _landed(_project(), ROWS)
    proposal = lifecycle.add_cell(dataset, title="Later", script=KEEP, proposed=True, note="why")
    real = lifecycle.add_cell(dataset, title="Now", script=SHAPE)
    real.refresh_from_db()
    proposal.refresh_from_db()
    assert real.position == 1 and proposal.position == 2
    assert proposal.id not in dataset.versions()
    lifecycle.accept_proposal(dataset, proposal)
    proposal.refresh_from_db()
    assert proposal.state == "queued"
    run_svc.execute(dataset)
    assert dataset.versions()[proposal.id] == "1.2"


def test_removing_a_cell_renumbers_and_queues_the_rest():
    dataset = _landed(_project(), ROWS)
    keep = lifecycle.add_cell(dataset, title="Keep", script=KEEP)
    shape = lifecycle.add_cell(dataset, title="Shape", script=SHAPE)
    run_svc.execute(dataset)
    lifecycle.remove_cell(dataset, keep)
    shape.refresh_from_db()
    assert shape.position == 1 and shape.state == "queued"
    assert not paths.cell_path(dataset.id, keep.id).exists()


def test_use_refuses_a_wrong_intent_and_a_failing_contract():
    dataset = _landed(_project(), ROWS, intent="eval")
    with pytest.raises(lifecycle.DatasetError, match="needs train"):
        use.use(dataset, "train")
    with pytest.raises(lifecycle.DatasetError, match="no input column"):
        use.use(dataset, "eval")


def test_use_marks_the_cell_and_starts_a_new_major():
    dataset = _landed(_project(), ROWS, intent="eval")
    keep = lifecycle.add_cell(dataset, title="Keep", script=KEEP)
    shape = lifecycle.add_cell(dataset, title="Shape", script=SHAPE)
    run_svc.execute(dataset)
    cell = use.use(dataset, "eval")
    assert cell == shape and cell.used_at is not None
    assert dataset.versions()[shape.id] == "2.0"
    later = lifecycle.add_cell(dataset, title="More", script="df = df\n")
    run_svc.execute(dataset)
    assert dataset.versions()[later.id] == "2.1"
    # Everything the used cell reads is frozen with it.
    keep.refresh_from_db()
    assert keep.frozen and shape.frozen and not later.frozen
    with pytest.raises(lifecycle.DatasetError, match="frozen"):
        lifecycle.edit_cell(dataset, keep, script="df = df\n")
    with pytest.raises(lifecycle.DatasetError, match="fixed"):
        lifecycle.set_intent(dataset, "train")
    # A second use of the same cell changes nothing.
    assert use.use(dataset, "eval", cell=shape).used_at == cell.used_at
    assert dataset.versions()[shape.id] == "2.0"


def test_a_used_cell_is_protected_and_blocks_deletion():
    project = _project()
    dataset = _landed(project, [dict(r) for r in EVAL_ROWS], intent="eval")
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


@pytest.mark.parametrize(
    ("script", "message"),
    [
        (
            "df = pd.concat([df, df[['question']]], axis=1)\n",
            "more than one column named 'question'",
        ),
        (
            "df.columns = pd.MultiIndex.from_tuples([('a', c) for c in df.columns])\n",
            "two levels of column names",
        ),
    ],
)
def test_a_frame_the_store_cannot_name_is_refused_in_words(tmp_path, script, message):
    result = runner.run(script, _source(tmp_path), library_cache=tmp_path / "none")
    assert not result.ok and message in result.error


def test_a_named_index_is_kept_as_columns(tmp_path):
    result = runner.run(
        "df = df.set_index(['tag', 'question'])\n",
        _source(tmp_path),
        library_cache=tmp_path / "none",
    )
    assert result.ok and {"tag", "question", "answer"} <= set(result.frame.columns)
