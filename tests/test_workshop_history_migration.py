from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings


def test_cutover_preserves_published_cells_and_provider_receipts(tmp_path, django_db_blocker):
    original = connections["default"]
    database = DatabaseWrapper(
        {**original.settings_dict, "NAME": str(tmp_path / "workshop-cutover.sqlite3")},
        alias="default",
    )
    with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
        connections["default"] = database
        try:
            executor = MigrationExecutor(database)
            before = [("overbae", "0025_dataset_import_artifact")]
            executor.migrate(before)
            old = executor.loader.project_state(before).apps
            project = old.get_model("overbae", "Project").objects.create(
                name="Migration", slug="migration"
            )
            dataset = old.get_model("overbae", "Dataset").objects.create(
                project=project,
                name="History",
                state="diagnosing",
                chat=[{"role": "agent", "text": "Saved history"}],
                agent_id="provider-agent",
                operation={
                    "provider": {"run_id": "unresolved-provider-run", "state": "submitting"}
                },
            )
            cell = old.get_model("overbae", "Cell").objects.create(
                dataset=dataset,
                position=0,
                title="Source",
                state="ok",
                fingerprint="a" * 64,
            )
            run = old.get_model("overbae", "WorkshopRun").objects.create(
                dataset=dataset, source=cell
            )
            item = old.get_model("overbae", "WorkshopWorkItem").objects.create(
                run=run,
                key="batch",
                provider={"receipt": "provider-receipt"},
                artifact="retained.parquet",
            )
            executor = MigrationExecutor(database)
            target = [("overbae", "0026_dataset_history")]
            executor.migrate(target)
            current = executor.loader.project_state(target).apps
            assert (
                current.get_model("overbae", "Cell").objects.get(pk=cell.pk).fingerprint == "a" * 64
            )
            assert (
                current.get_model("overbae", "Dataset").objects.get(pk=dataset.pk).state == "idle"
            )
            history = current.get_model("overbae", "DatasetHistory")
            assert (
                history.objects.get(dataset_id=dataset.pk, kind="conversation").payload["chat"][0][
                    "text"
                ]
                == "Saved history"
            )
            assert (
                history.objects.get(dataset_id=dataset.pk, kind="operation").payload["provider"][
                    "run_id"
                ]
                == "unresolved-provider-run"
            )
            assert (
                history.objects.get(
                    dataset_id=dataset.pk, kind="work_item", reference=str(item.pk)
                ).payload["provider"]["receipt"]
                == "provider-receipt"
            )
            assert "overbae_workshoprun" not in database.introspection.table_names()

        finally:
            database.close()
            connections["default"] = original
