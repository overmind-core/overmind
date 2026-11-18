"""Tables move through landing, a cell, paging, the diff and a sandboxed pipeline inside
budgets a per-row Python loop or a quadratic join would miss by a wide margin."""

from __future__ import annotations

import io
import json
import time
import uuid
import zipfile
from unittest import mock

import pytest

from overbae.models import Dataset, Project
from overbae.services.datasets import (
    diff,
    land,
    paths,
    pipeline_packages,
    pipeline_runner,
    store,
    workbench,
)

pytestmark = [pytest.mark.django_db, pytest.mark.load, pytest.mark.covers("capacity")]
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
    dataset = Dataset.objects.create(project=project, name="big", intent="train")
    _timed(20, lambda: land.land_rows(dataset, rows))
    dataset.refresh_from_db()
    assert dataset.intent == "train" and dataset.source.fits("train") == (True, "")

    from conftest import import_version

    selected = [
        r for r in store.iter_rows(paths.cell_path(dataset.pk, dataset.source.pk)) if r["tag"] != 3
    ]
    cell = _timed(30, lambda: import_version(dataset, selected, name="Drop a tag"))
    cell.refresh_from_db()
    assert cell.state == "ok" and cell.rows == ROWS - len(range(3, ROWS, 7))

    before = paths.cell_path(dataset.id, dataset.source.id)
    after = paths.cell_path(dataset.id, cell.id)
    page = _timed(2, lambda: store.page(after, offset=cell.rows - 50, limit=50, sort="tag"))
    assert len(page["rows"]) == 50
    summary = _timed(2, lambda: diff.summary(before, after))
    assert summary["rows_removed"] == ROWS - cell.rows


DECIDE = """import json, sys
with open(sys.argv[1]) as source, open(sys.argv[2], "w") as output:
    for line in source:
        row = json.loads(line)
        row["decision"] = {"state": json.dumps(row["items"]), "question": "Status?",
            "kind": "choice", "options": ["shipped", "refunded"],
            "target_probabilities": [float(row["status"] == "shipped"), float(row["status"] == "refunded")]}
        output.write(json.dumps(row) + "\\n")
"""


def test_three_hundred_thousand_orders_land_and_publish_a_batched_script_within_budget(tmp_path):
    rows = 300_000
    source = tmp_path / "orders.jsonl"
    with source.open("w") as stream:
        for i in range(rows):
            order = {
                "order_id": f"O{i:08d}",
                "customer": {"id": i % 9000, "tier": "gold" if i % 5 else None},
                "items": [{"sku": f"S{i % 400}", "qty": 1 + i % 3}] * (1 + i % 4),
                "status": "shipped" if i % 3 else "refunded",
            }
            stream.write(json.dumps(order) + "\n")
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    dataset = Dataset.objects.create(project=project, name="orders")
    _timed(60, lambda: land.commit(dataset, land.read_file(source, filename="orders.jsonl")))
    dataset.refresh_from_db()
    original = dataset.source
    manifest = {
        "version": 1,
        "runtime": pipeline_packages.runtime_id(),
        "steps": [
            {
                "consumer": "decision_train",
                "entrypoint": "decide.py",
                "id": "decide",
                "input": "source",
                "name": "Decide",
                "batch_rows": 2000,
                "checks": {"preserve_rows": True},
            }
        ],
    }
    bundle = io.BytesIO()
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("decide.py", DECIDE)
    bundle.seek(0)
    package = pipeline_packages.save(project, None, bundle)
    pipeline = workbench.save_pipeline(
        project, None, name="Decide", request_key="decide", package=package.pk, dataset=dataset
    )
    with mock.patch("overbae.tasks.datasets.execute_pipeline.delay"):
        run = workbench.submit(
            dataset,
            None,
            source_cell=original.pk,
            source_fingerprint=original.fingerprint,
            request_key="decide",
            pipeline=pipeline.pk,
        )
    _timed(180, lambda: workbench.execute(run.pk, script_executor=pipeline_runner.execute_script))
    run.refresh_from_db()
    dataset.refresh_from_db()
    assert run.state == "completed", run.error
    assert dataset.active_cell.rows == rows
    assert (dataset.intent, dataset.train_format) == ("train", "decision")
