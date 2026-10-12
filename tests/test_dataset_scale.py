"""A 20,000-row table moves through landing, a sandboxed version, paging and the diff
inside budgets a per-row Python loop or a quadratic join would miss by a wide margin."""

from __future__ import annotations

import time
import uuid

import pytest

from overbae.models import Dataset, Execution, Project
from overbae.services.datasets import diff, executions, land, paths, store

pytestmark = pytest.mark.django_db
ROWS = 20_000


def _timed(budget: float, fn):
    started = time.monotonic()
    result = fn()
    assert time.monotonic() - started < budget
    return result


def test_twenty_thousand_transcripts_land_run_page_and_diff_within_budget():
    rows = [
        {
            "messages": [
                {"role": "system", "content": "You triage tickets."},
                {"role": "user", "content": f"ticket {i} " + "x" * 400},
                {"role": "assistant", "content": "y" * 400},
            ],
            "tag": i % 7,
        }
        for i in range(ROWS)
    ]
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    dataset = Dataset.objects.create(project=project, name="big", intent="pending")
    _timed(20, lambda: land.land_rows(dataset, rows))
    dataset.refresh_from_db()
    assert dataset.intent == "train" and dataset.source.fits("train") == (True, "")

    done = _timed(
        30,
        lambda: executions.run_now(
            dataset,
            mode=Execution.Mode.CELL,
            parent=dataset.source,
            script="df = df[df.tag != 3]\n",
            title="Drop a tag",
        ),
    )
    cell = done.cell
    assert cell is not None, done.run.error
    assert cell.rows == ROWS - len(range(3, ROWS, 7))

    before = paths.cell_path(dataset.id, dataset.source.id)
    after = paths.cell_path(dataset.id, cell.id)
    page = _timed(2, lambda: store.page(after, offset=cell.rows - 50, limit=50, sort="tag"))
    assert len(page["rows"]) == 50
    summary = _timed(2, lambda: diff.summary(before, after))
    assert summary["rows_removed"] == ROWS - cell.rows
