from importlib import import_module
from types import SimpleNamespace

from test_innermind_migration import scratch_database as scratch_database

migration = import_module("overbae.migrations.0016_innermind")


def test_legacy_sample_preserves_generated_output_and_evidence():
    sample = SimpleNamespace(
        pk="sample-1",
        variant_id="variant-1",
        row_index=7,
        source_trace_id="trace-1",
        trajectory={"final_output": "answer", "metadata": {"model": "old-model"}},
        structured={"turns": []},
        expected={"answer": "reference"},
        context_coverage=0.75,
        error="",
        degraded=False,
        degraded_reason="",
        celery_task_id="task-1",
    )

    row = migration.legacy_sample_values(sample)

    assert row["output"] == "answer"
    assert row["metadata"]["model"] == "old-model"
    assert row["metadata"]["legacy_sample"]["expected"] == {"answer": "reference"}
    assert row["metadata"]["legacy_sample"]["structured"] == {"turns": []}
    assert row["source_trace_id"] == "trace-1"


def test_legacy_scores_keep_outcomes_and_checklist_evidence():
    scores = [
        SimpleNamespace(
            name="Task Success",
            outcome="scored",
            value=0.8,
            passed=True,
            reasoning="Mostly correct",
            scope="final_output",
            sub_scores=[{"id": "check-1", "verdict": True}],
        ),
        SimpleNamespace(
            name="Evidence",
            outcome="error",
            value=None,
            passed=None,
            reasoning="Judge unavailable",
            scope="final_output",
            sub_scores=[],
        ),
    ]

    value, metrics = migration.legacy_score_values(scores)

    assert value == 0.8
    assert metrics["Task Success"]["score"] == 0.8
    assert metrics["Task Success"]["sub_scores"] == scores[0].sub_scores
    assert metrics["Evidence"]["error"] == "Judge unavailable"
    assert metrics["Evidence"]["score"] is None


def test_cutover_preserves_variants_and_samples_without_dataset_indices(
    scratch_database, django_db_blocker
):
    from django.db import connections
    from django.db.backends.postgresql.base import DatabaseWrapper
    from django.db.migrations.executor import MigrationExecutor
    from django.test import override_settings

    original = connections["default"]
    database = DatabaseWrapper(
        {**original.settings_dict, "NAME": scratch_database}, alias="default"
    )
    with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
        connections["default"] = database
        try:
            executor = MigrationExecutor(database)
            before = [("overbae", "0015_workshop_execution_clock")]
            executor.migrate(before)
            old = executor.loader.project_state(before).apps
            project = old.get_model("overbae", "Project").objects.create(
                name="Migration", slug="migration"
            )
            run = old.get_model("overbae", "EvalRun").objects.create(
                project=project, name="Legacy", status="completed"
            )
            variants = [
                old.get_model("overbae", "EvalVariant").objects.create(
                    run=run, label=label, order=i
                )
                for i, label in enumerate(["Baseline", "Candidate"])
            ]
            for variant in variants:
                for i, index in enumerate([0, 0, None]):
                    sample = old.get_model("overbae", "EvalSample").objects.create(
                        run=run,
                        variant=variant,
                        row_index=index,
                        trajectory={"final_output": f"{variant.label}-{i}"},
                    )
                    old.get_model("overbae", "Score").objects.create(
                        project=project,
                        run=run,
                        variant=variant,
                        sample=sample,
                        name="Correct",
                        outcome="scored",
                        value=0.8,
                    )
            executor = MigrationExecutor(database)
            target = [("overbae", "0016_innermind")]
            executor.migrate(target)
            current = executor.loader.project_state(target).apps
            runs = current.get_model("overbae", "EvalRun").objects.all()
            assert runs.count() == 2
            for migrated_run in runs:
                rows = list(
                    current.get_model("overbae", "EvalRunRow").objects.filter(run=migrated_run)
                )
                assert len(rows) == 3
                assert len({row.row_index for row in rows}) == 3
                assert {row.output for row in rows} == {
                    f"{migrated_run.params['label']}-{i}" for i in range(3)
                }
                assert [row.metadata["legacy_sample"]["row_index"] for row in rows] == [0, 0, None]
                scores = current.get_model("overbae", "EvalScore").objects.filter(run=migrated_run)
                assert set(scores.values_list("source_id", flat=True)) == {
                    f"None:{row.row_index}" for row in rows
                }
                assert migrated_run.summary["rows"] == 3
        finally:
            database.close()
            connections["default"] = original
