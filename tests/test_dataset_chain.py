"""Versions: landing, scripts run in the sandbox, the version tree, diff and the use gate."""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest
from conftest import EVAL_ROWS, TRAIN_ROWS, read_table
from django.db.models import RestrictedError
from factories import make_project

from overbae.models import Capability, Cell, Dataset, EvalRun, Execution, Project
from overbae.services.datasets import diff, executions, land, lifecycle, paths, store, use, versions
from sandbox import execute

pytestmark = pytest.mark.django_db


def _landed(project: Project, rows, name="ds", intent="pending") -> Dataset:
    dataset = Dataset.objects.create(project=project, name=name, intent=intent)
    land.land_rows(dataset, [dict(r) for r in rows])
    dataset.refresh_from_db()
    return dataset


def _run(dataset, parent, script, *, mode=Execution.Mode.CELL, title="Step"):
    return executions.run_now(dataset, mode=mode, parent=parent, script=script, title=title)


def _version(dataset, parent, script, title="Step") -> Cell:
    done = _run(dataset, parent, script, title=title)
    assert done.cell is not None, done.run.error
    return done.cell


ROWS = [
    {"question": "q1", "answer": "a1", "tag": "keep"},
    {"question": "", "answer": "orphan", "tag": "junk"},
    {"question": "q3", "answer": "a3", "tag": "keep"},
]
KEEP = "df = df[df['tag'] == 'keep']\n"
SHAPE = "df = df.rename(columns={'question': 'input', 'answer': 'expected_output'})\n"


def _sandbox(budget: int):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    token = secrets.token_hex(8)
    proc = subprocess.Popen(
        [sys.executable, "-m", "sandbox", "--host", "127.0.0.1", "--port", str(port)],
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "SANDBOX_TOKEN": token,
            "SANDBOX_ALLOW_SHARED_UID": "1",
            "SANDBOX_MEMORY_BYTES": str(budget),
        },
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 30
    while True:
        try:
            if httpx.get(f"{url}/health", timeout=1).status_code == 200:
                break
        except httpx.TransportError:
            pass
        assert proc.poll() is None and time.monotonic() < deadline, "sandbox did not start"
        time.sleep(0.1)
    try:
        yield url, token
    finally:
        proc.terminate()
        proc.wait(timeout=10)


@pytest.fixture(scope="module")
def tiny_sandbox():
    """Half a GiB: below the smallest reservation, so no script starts."""
    yield from _sandbox(512 * 1024**2)


@pytest.fixture(scope="module")
def narrow_sandbox():
    """1.5 GiB: one script at a time, since each reserves at least 1 GiB."""
    yield from _sandbox(3 * 1024**3 // 2)


def test_a_script_reads_df_and_keeps_source_row_through_a_filter():
    dataset = _landed(make_project(), ROWS)
    cell = _version(dataset, dataset.source, KEEP)
    frame = read_table(paths.cell_path(dataset.id, cell.id))
    assert list(frame["question"]) == ["q1", "q3"]
    assert list(frame["source_row"]) == [0, 2]


def test_source_row_is_rebuilt_when_the_script_dropped_it():
    dataset = _landed(make_project(), ROWS)
    cell = _version(dataset, dataset.source, "df = df[['question']][df['question'] != '']\n")
    assert list(read_table(paths.cell_path(dataset.id, cell.id))["source_row"]) == [0, 2]


@pytest.mark.parametrize(
    "script",
    [
        "df = df.drop(columns=['source_row']).iloc[::-1].reset_index(drop=True)\n",
        "df = pd.DataFrame(df.drop(columns=['source_row']).to_dict('records')[::-1])\n",
    ],
)
def test_rows_renumbered_after_dropping_source_row_lose_their_identity(script):
    dataset = _landed(make_project(), ROWS)
    cell = _version(dataset, dataset.source, script)
    assert "source_row" not in read_table(paths.cell_path(dataset.id, cell.id)).columns
    assert cell.impact["identity_preserved"] is False


def test_source_row_follows_reordered_rows_that_kept_their_labels():
    dataset = _landed(make_project(), ROWS)
    cell = _version(dataset, dataset.source, "df = df.drop(columns=['source_row']).iloc[::-1]\n")
    frame = read_table(paths.cell_path(dataset.id, cell.id))
    assert list(frame["source_row"]) == [2, 1, 0]


def test_a_reshape_without_row_identity_has_no_source_row():
    dataset = _landed(make_project(), ROWS)
    cell = _version(
        dataset, dataset.source, "df = df.groupby('tag').size().reset_index(name='n')\n"
    )
    assert "source_row" not in read_table(paths.cell_path(dataset.id, cell.id)).columns
    assert cell.impact["identity_preserved"] is False


def test_a_failing_script_creates_no_version_and_carries_the_traceback():
    dataset = _landed(make_project(), ROWS)
    done = _run(dataset, dataset.source, "df = df['missing']\n")
    assert done.cell is None
    assert done.run.state == Execution.State.FAILED and not done.run.transient
    assert "missing" in done.run.error
    dataset.refresh_from_db()
    assert (dataset.state, dataset.active_id) == ("idle", dataset.source.id)
    assert dataset.cells.count() == 1


def test_a_script_larger_than_the_sandbox_budget_does_not_start(settings, tiny_sandbox):
    settings.SANDBOX_URL, settings.SANDBOX_TOKEN = tiny_sandbox
    dataset = _landed(make_project(), ROWS)
    done = _run(dataset, dataset.source, KEEP)
    assert done.run.state == Execution.State.FAILED
    assert done.run.error == (
        "This version needs about 1.0 GiB of memory for a pandas cell; this worker has 0.5 GiB."
    )


def test_scripts_that_do_not_fit_together_take_turns(narrow_sandbox, tmp_path):
    from test_sandbox import run

    url, token = narrow_sandbox
    frame = tmp_path / "in.parquet"
    store.write_rows(frame, [{"source_row": 0, "x": 1}])

    script = "import time\nstart = time.time()\ntotal = sum(range(20_000_000))\nprint(start, time.time())\n"

    def span(_):
        _status, header, _ = run(url, token, script, frame)
        assert header["ok"], header["error"]
        return tuple(float(value) for value in header["stdout"].split())

    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = sorted(pool.map(span, range(2)))
    assert first[1] <= second[0]


def test_the_audit_refuses_imports_and_calls_outside_the_list():
    assert execute.audit("import sklearn\nimport re\n") == []
    assert execute.audit("import nltk\n") == ["import nltk is not allowed"]
    assert execute.audit("open('x')\n") == ["open() is not allowed"]
    assert execute.audit("import os\nos.system('x')\n") == ["system is not allowed"]


def test_the_audit_lets_methods_share_a_name_with_a_refused_builtin():
    script = "import re\npattern = re.compile('a+')\ndf = df[df.eval('rows > 1')]\n"
    assert execute.audit(script) == []


def test_landing_writes_v0_makes_it_active_and_proposes_the_intent():
    dataset = _landed(make_project(), TRAIN_ROWS)
    source = dataset.source
    assert source is not None and source.position == 0 and source.parent_id is None
    assert source.rows == len(TRAIN_ROWS)
    assert (dataset.intent, dataset.state, dataset.active_id) == ("train", "idle", source.id)
    assert source.label == "v0"
    assert paths.cell_path(dataset.id, source.id).exists()


def test_landing_keeps_a_chosen_intent_and_ranks_capabilities():
    project = make_project()
    capability = Capability.objects.create(project=project, name="KB", slug="kb")
    rows = [{**r, "capability_id": str(capability.id)} for r in EVAL_ROWS]
    dataset = _landed(project, rows, intent="train")
    assert dataset.intent == "train"
    assert dataset.capability_id == capability.id
    assert dataset.capability_rank[0]["capability_id"] == str(capability.id)
    assert dataset.capability_rank[0]["score"] == 1.0


def test_versions_chain_by_parent_are_numbered_and_never_become_active_alone():
    dataset = _landed(make_project(), ROWS)
    keep = _version(dataset, dataset.source, KEEP, "Keep")
    shape = _version(dataset, keep, SHAPE, "Shape")
    dataset.refresh_from_db()
    assert (keep.label, shape.label) == ("v1", "v2")
    assert (keep.parent_id, shape.parent_id) == (dataset.source.id, keep.id)
    assert keep.rows == 2 and keep.input_fingerprint == dataset.source.fingerprint
    assert shape.input_fingerprint == keep.fingerprint
    assert keep.impact["rows_removed"] == 1 and keep.impact["rows_after"] == 2
    assert dataset.active_id == dataset.source.id
    versions.activate(dataset, shape)
    assert dataset.active_cell == shape
    assert shape.fits("eval") == (True, "")


def test_the_same_script_under_the_same_parent_returns_the_existing_version():
    dataset = _landed(make_project(), ROWS)
    keep = _version(dataset, dataset.source, KEEP, "Keep")
    again = _run(dataset, dataset.source, KEEP, title="Another title")
    assert again.cell == keep and again.run is None
    assert dataset.executions.filter(mode=Execution.Mode.CELL).count() == 1


def test_different_scripts_under_one_parent_are_sibling_branches():
    dataset = _landed(make_project(), ROWS)
    keep = _version(dataset, dataset.source, KEEP)
    first = _version(dataset, keep, "df = df[df['question'] != 'q1']\n")
    second = _version(dataset, keep, "df['flag'] = df['question'] == 'q1'\n")
    assert first.parent_id == second.parent_id == keep.id
    assert (first.label, second.label) == ("v2", "v3")
    assert set(keep.children.values_list("pk", flat=True)) == {first.id, second.id}


def test_a_version_with_children_cannot_be_deleted_but_the_dataset_takes_the_tree():
    dataset = _landed(make_project(), ROWS)
    keep = _version(dataset, dataset.source, KEEP)
    _version(dataset, keep, SHAPE)
    with pytest.raises(RestrictedError):
        keep.delete()
    lifecycle.delete_dataset(dataset)
    assert not Cell.objects.filter(dataset_id=dataset.id).exists()
    assert not paths.dataset_dir(dataset.id).exists()


def test_use_refuses_a_wrong_intent_and_a_failing_contract():
    dataset = _landed(make_project(), ROWS, intent="eval")
    with pytest.raises(lifecycle.DatasetError, match="needs train"):
        use.use(dataset, "train")
    with pytest.raises(lifecycle.DatasetError, match="no input column"):
        use.use(dataset, "eval")


def test_use_freezes_the_version_and_fixes_the_intent():
    dataset = _landed(make_project(), ROWS, intent="eval")
    keep = _version(dataset, dataset.source, KEEP)
    shape = _version(dataset, keep, SHAPE)
    versions.activate(dataset, shape)
    cell = use.use(dataset, "eval")
    assert cell == shape and cell.used_at is not None and cell.frozen
    keep.refresh_from_db()
    assert not keep.frozen
    with pytest.raises(lifecycle.DatasetError, match="fixed"):
        lifecycle.set_intent(dataset, "train")
    assert use.use(dataset, "eval", cell=shape).used_at == cell.used_at


def test_a_used_version_is_protected_and_blocks_deletion():
    project = make_project()
    dataset = _landed(project, EVAL_ROWS, intent="eval")
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
def test_a_frame_the_store_cannot_name_is_refused_in_words(script, message):
    dataset = _landed(make_project(), ROWS)
    done = _run(dataset, dataset.source, script, mode=Execution.Mode.TRY)
    assert done.run.state == Execution.State.FAILED and message in done.run.error


def test_a_named_index_is_kept_as_columns():
    dataset = _landed(make_project(), ROWS)
    done = _run(dataset, dataset.source, "df = df.set_index(['tag', 'question'])\n", mode="try")
    assert done.run.state == Execution.State.OK
    assert {"tag", "question", "answer"} <= set(done.run.result["columns"])
