import pytest
from django.db import connections
from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import override_settings
from django.utils import timezone
from test_workshop_training_migration import scratch_database as scratch_database

TARGET = ("overbae", "0034_source_import_recovery")


@pytest.mark.parametrize("start", ["0015_workshop_execution_clock", "0033_training_monitoring"])
def test_each_applied_branch_upgrades_without_losing_source_or_training_state(
    start,
    scratch_database,
    django_db_blocker,
):
    original = connections["default"]
    database = DatabaseWrapper(
        {**original.settings_dict, "NAME": scratch_database}, alias="default"
    )
    with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
        connections["default"] = database
        try:
            executor = MigrationExecutor(database)
            executor.migrate([("overbae", start)])
            apps = executor.loader.project_state([("overbae", start)]).apps
            project = apps.get_model("overbae", "Project").objects.create(name="Retained")
            dataset = apps.get_model("overbae", "Dataset").objects.create(
                project=project, name="Source", state="idle"
            )
            cell = apps.get_model("overbae", "Cell").objects.create(
                dataset=dataset, position=0, state="ok", fingerprint="retained-cell", rows=3
            )
            job = apps.get_model("overbae", "FinetuningJob").objects.create(
                project=project,
                dataset=dataset,
                cell=cell,
                status="succeeded",
                output_model_name="retained-model",
            )
            if start.startswith("0015"):
                dataset.workshop_queued_at = timezone.now()
                dataset.save(update_fields=["workshop_queued_at"])
                receipt = apps.get_model("overbae", "DatasetImport").objects.create(
                    dataset=dataset,
                    inputs={"source": {"rows": []}},
                    handoff_pending=True,
                    handoff_attempts=0,
                    queued_at=timezone.now(),
                )
            before = set(MigrationRecorder(database).applied_migrations())
            executor = MigrationExecutor(database)
            executor.migrate([TARGET])
            apps = executor.loader.project_state([TARGET]).apps
            retained = apps.get_model("overbae", "FinetuningJob").objects.get(pk=job.pk)
            assert retained.cell_id == cell.pk and retained.output_model_name == "retained-model"
            assert (
                apps.get_model("overbae", "Cell").objects.get(pk=cell.pk).fingerprint
                == "retained-cell"
            )
            assert before <= set(MigrationRecorder(database).applied_migrations())
            if start.startswith("0015"):
                assert (
                    apps.get_model("overbae", "DatasetImport").objects.get(pk=receipt.pk).inputs
                    == receipt.inputs
                )
                archived = {
                    row.kind: row.payload
                    for row in apps.get_model("overbae", "DatasetHistory").objects.filter(
                        dataset_id=dataset.pk
                    )
                }
                assert (
                    archived["workshop_ownership"]["queued_at"]
                    == dataset.workshop_queued_at.isoformat()
                )
                assert archived["import_handoff"]["pending"] is True
        finally:
            database.close()
            connections["default"] = original
