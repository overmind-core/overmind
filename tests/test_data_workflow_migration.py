from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings

BASE = ("overbae", "0018_workshop_sources_and_funding")
TARGET = ("overbae", "0021_data_exploration")


def test_workflow_migration_preserves_completed_native_results(tmp_path, django_db_blocker):
    original = connections["default"]
    database = DatabaseWrapper(
        {**original.settings_dict, "NAME": str(tmp_path / "workflow-migration.sqlite3")},
        alias="default",
    )
    with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
        connections["default"] = database
        try:
            executor = MigrationExecutor(database)
            executor.migrate([BASE])
            apps = executor.loader.project_state([BASE]).apps
            project = apps.get_model("overbae", "Project").objects.create(name="Frozen experiment")
            dataset = apps.get_model("overbae", "Dataset").objects.create(
                project=project, name="Final", intent="eval"
            )
            cell = apps.get_model("overbae", "Cell").objects.create(
                dataset=dataset, position=0, state="ok", fingerprint="unchanged"
            )
            calibration = apps.get_model("overbae", "Cell").objects.create(
                dataset=dataset, position=1, state="ok", fingerprint="calibration"
            )
            job = apps.get_model("overbae", "FinetuningJob").objects.create(
                project=project, dataset=dataset, name="Saved", base_model="Qwen/Qwen3.5-4B"
            )
            plan = apps.get_model("overbae", "NativeEvaluationPlan").objects.create(
                job=job,
                final_cell=cell,
                calibration_cell=calibration,
                state="completed",
                config={"runtime": {"app": "original"}},
                calls={"final_candidate": {"call_id": "fc-original"}},
                calibration={"frozen": True},
                results={"original": 0.32},
            )
            executor = MigrationExecutor(database)
            executor.migrate([TARGET])
            apps = executor.loader.project_state([TARGET]).apps
            restored = apps.get_model("overbae", "NativeEvaluationPlan").objects.get(pk=plan.pk)
            assert restored.project_id == project.pk
            assert restored.calls == {"final_candidate": {"call_id": "fc-original"}}
            assert restored.calibration == {"frozen": True}
            assert restored.results == {"original": 0.32}
            assert restored.config["runtime"] == {"app": "original"}
            assert restored.config["logarithm_floor"] is None
            assert restored.final_cell_id == cell.pk
            assert restored.state == "completed"
            assert apps.get_model("overbae", "DecisionPerformanceRun").objects.count() == 0
            assert apps.get_model("overbae", "DataExploration").objects.count() == 0
        finally:
            database.close()
            connections["default"] = original
