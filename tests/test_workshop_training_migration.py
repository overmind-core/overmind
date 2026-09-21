from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.recorder import MigrationRecorder
from django.test import override_settings

TARGET = ("overbae", "0005_workshop_training")
BASE = ("overbae", "0004_finetuningjob_eval_cell")
BEAT = ("django_celery_beat", "0001_initial")


def test_consolidated_migration_preserves_data_and_applied_history(tmp_path, django_db_blocker):
    original = connections["default"]
    database = DatabaseWrapper(
        {**original.settings_dict, "NAME": str(tmp_path / "migration.sqlite3")}, alias="default"
    )
    with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
        connections["default"] = database
        try:
            executor = MigrationExecutor(database)
            executor.migrate([BASE, BEAT])
            apps = executor.loader.project_state([BASE, BEAT]).apps
            project = apps.get_model("overbae", "Project").objects.create(name="Migration")
            dataset = apps.get_model("overbae", "Dataset").objects.create(
                project=project, name="Evaluation", intent="eval"
            )
            cell_model = apps.get_model("overbae", "Cell")
            pinned = cell_model.objects.create(
                dataset=dataset, position=0, state="ok", fingerprint="old"
            )
            active = cell_model.objects.create(
                dataset=dataset, position=1, state="ok", fingerprint="new"
            )
            dataset.active = active
            dataset.save(update_fields=["active"])
            job_model = apps.get_model("overbae", "FinetuningJob")
            job = job_model.objects.create(project=project, dataset=dataset, eval_dataset=dataset)
            fallback = job_model.objects.create(
                project=project, dataset=dataset, eval_dataset=dataset
            )
            already_pinned = job_model.objects.create(
                project=project, dataset=dataset, eval_dataset=dataset, eval_cell=pinned
            )
            run = apps.get_model("overbae", "EvalRun").objects.create(
                project=project, dataset=dataset, cell=pinned
            )
            apps.get_model("overbae", "FinetuningJobEval").objects.create(
                job=job, eval_run=run, kind="baseline"
            )
            task_model = apps.get_model("django_celery_beat", "PeriodicTask")
            janitor = task_model.objects.create(
                name="Old janitor", task="overbae.tasks.inference_controller.janitor_stuck_fsm"
            )
            other = task_model.objects.create(name="Other task", task="other.task")

            executor = MigrationExecutor(database)
            executor.migrate([TARGET])
            apps = executor.loader.project_state([TARGET]).apps
            job_model = apps.get_model("overbae", "FinetuningJob")
            assert job_model.objects.get(pk=job.pk).eval_cell_id == pinned.pk
            assert job_model.objects.get(pk=fallback.pk).eval_cell_id == active.pk
            assert job_model.objects.get(pk=already_pinned.pk).eval_cell_id == pinned.pk
            assert job_model.objects.get(pk=job.pk).eval_incumbent_before is True
            assert job_model.objects.get(pk=job.pk).eval_model_before is False
            assert job_model._meta.get_field("eval_incumbent_before").default is False
            assert job_model._meta.get_field("eval_model_before").default is True
            task_model = apps.get_model("django_celery_beat", "PeriodicTask")
            assert task_model.objects.get(pk=janitor.pk).enabled is False
            assert task_model.objects.get(pk=other.pk).enabled is True
            assert apps.get_model("overbae", "TrainingPreparation").objects.count() == 0

            # A database upgraded with the nine original files must not replay their DDL.
            recorder = MigrationRecorder(database)
            recorder.record_unapplied(*TARGET)
            executor = MigrationExecutor(database)
            assert executor.migration_plan([TARGET]) == []
            executor.migrate([TARGET])
            assert TARGET in recorder.applied_migrations()
            assert job_model.objects.count() == 3
        finally:
            database.close()
            connections["default"] = original
