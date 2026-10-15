"""Admission and fairness queries must avoid completed evaluation history."""

import json
import uuid

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from overbae.innermind import generation
from overbae.models import EvalRun, EvalRunRow, Project

pytestmark = pytest.mark.django_db(transaction=True)


def examined_history_rows(node):
    examined = 0
    if node.get("Relation Name") in {"overbae_evalrun"}:
        examined = (
            node.get("Actual Rows", 0)
            + node.get("Rows Removed by Filter", 0)
            + node.get("Rows Removed by Index Recheck", 0)
        ) * node.get("Actual Loops", 1)
    return examined + sum(examined_history_rows(child) for child in node.get("Plans", []))


def test_admission_queries_do_not_walk_completed_run_history(settings, tmp_path):
    settings.EVAL_MAX_IN_FLIGHT = 1
    settings.EVAL_MAX_IN_FLIGHT_PER_RUN = 1
    project = Project.objects.create(name="Evaluation history", slug=uuid.uuid4().hex)
    EvalRun.objects.bulk_create(
        [
            EvalRun(project=project, name=f"Old {index}", status="completed")
            for index in range(4000)
        ],
        batch_size=500,
    )
    for index in range(2):
        run = EvalRun.objects.create(project=project, name=f"Live {index}", status="running")
        EvalRunRow.objects.create(run=run, row_index=0)
    with connection.cursor() as cursor:
        for table in (
            "overbae_evalrun",
            "overbae_evalrunrow",
        ):
            cursor.execute(f"ANALYZE {table}")
    published, scored = [], []
    with CaptureQueriesContext(connection) as captured:
        result = generation.dispatch(
            lambda *args: published.append(args), lambda *args: scored.append(args)
        )
    assert result["admitted"] == 1 and len(published) == 1 and not scored
    queries = [
        item["sql"]
        for item in captured
        if "overbae_evalrun" in item["sql"] and item["sql"].lstrip().startswith("SELECT")
    ]
    assert queries
    plans = []
    for query in queries:
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + query)
            explained = cursor.fetchone()[0]
        plans.append(
            {
                "sql": query,
                "plan": explained,
                "examined": examined_history_rows(explained[0]["Plan"]),
            }
        )
    (tmp_path / "evaluation-capacity-plans.json").write_text(
        json.dumps({"terminal_runs": 4000, "live_runs": 2, "plans": plans}, indent=2, default=str)
    )
    assert max(plan["examined"] for plan in plans) < 100, (
        f"Admission examined completed history with only2 live runs: {plans}"
    )
