"""Control work must stay proportional to live imports, not retained history."""

import json
import uuid
from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from overbae.models import Dataset, DatasetImport, Project
from overbae.services.datasets import imports
from overbae.services.queue_capacity import read_workloads

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def receipt_history():
    now = timezone.now()
    project = Project.objects.create(name="Import history", slug=f"history-{uuid.uuid4().hex[:8]}")
    history = Dataset.objects.bulk_create(
        [
            Dataset(project=project, name=f"Completed {index}", state="idle")
            for index in range(4000)
        ],
        batch_size=500,
    )
    DatasetImport.objects.bulk_create(
        [
            DatasetImport(
                dataset=dataset, state="complete", queued_at=now - timedelta(days=30), inputs={}
            )
            for dataset in history
        ],
        batch_size=500,
    )
    for state in ("queued", "running", "blocked"):
        dataset = Dataset.objects.create(project=project, name=state)
        DatasetImport.objects.create(
            dataset=dataset,
            state=state,
            queued_at=now,
            inputs={},
            published_at=now if state == "queued" else None,
            lease_until=now + timedelta(minutes=60) if state == "running" else None,
        )
    with connection.cursor() as cursor:
        cursor.execute("ANALYZE overbae_datasetimport")
    return now


def examined_import_rows(node):
    examined = 0
    if node.get("Relation Name") == "overbae_datasetimport":
        examined = (
            node.get("Actual Rows", 0)
            + node.get("Rows Removed by Filter", 0)
            + node.get("Rows Removed by Index Recheck", 0)
        ) * node.get("Actual Loops", 1)
    return examined + sum(examined_import_rows(child) for child in node.get("Plans", []))


@pytest.mark.parametrize("operation", ["reconcile", "metrics"])
def test_control_queries_do_not_walk_completed_import_history(receipt_history, operation, tmp_path):
    with CaptureQueriesContext(connection) as captured:
        if operation == "reconcile":
            assert imports.reconcile() == {"processed": 0}
        else:
            workload = read_workloads()["landing"]
            assert (workload["waiting"], workload["running"], workload["blocked"]) == (1, 1, 1)
    queries = [
        item["sql"]
        for item in captured
        if "overbae_datasetimport" in item["sql"] and item["sql"].lstrip().startswith("SELECT")
    ]
    assert len(queries) == 1
    with connection.cursor() as cursor:
        cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + queries[0])
        explained = cursor.fetchone()[0]
    artifact = {
        "operation": operation,
        "terminal_receipts": 4000,
        "live_receipts": 3,
        "sql": queries[0],
        "plan": explained,
    }
    (tmp_path / f"{operation}-capacity-plan.json").write_text(
        json.dumps(artifact, indent=2, default=str)
    )
    examined = examined_import_rows(explained[0]["Plan"])
    assert examined < 100, (
        f"{operation} examined {examined} import rows for only3 live receipts; plan: {explained}"
    )
