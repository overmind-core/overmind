from django.db import connections
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings


def test_project_recipe_cutover_preserves_history_and_resolves_request_collisions(
    tmp_path, django_db_blocker
):
    original = connections["default"]
    database = DatabaseWrapper(
        {**original.settings_dict, "NAME": str(tmp_path / "recipes.sqlite3")}, alias="default"
    )
    with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
        connections["default"] = database
        try:
            executor = MigrationExecutor(database)
            before = [("overbae", "0031_dataset_transfer")]
            executor.migrate(before)
            old = executor.loader.project_state(before).apps
            project = old.get_model("overbae", "Project").objects.create(
                name="Existing", slug="existing"
            )
            identities = []
            for number in range(2):
                dataset = old.get_model("overbae", "Dataset").objects.create(
                    project=project, name=str(number)
                )
                cell = old.get_model("overbae", "Cell").objects.create(
                    dataset=dataset, position=1, fingerprint="a" * 64
                )
                recipe = old.get_model("overbae", "DatasetPipeline").objects.create(
                    dataset=dataset,
                    name="Projection",
                    request_key="same-key",
                    fingerprint="b" * 64,
                    steps=[{"operation": "select", "columns": ["value"]}],
                )
                run = old.get_model("overbae", "DatasetPipelineRun").objects.create(
                    dataset=dataset,
                    pipeline=recipe,
                    source=cell,
                    output=cell,
                    source_fingerprint="a" * 64,
                    request_key="run",
                    fingerprint="c" * 64,
                    state="completed",
                    result={"rows": 10000},
                )
                identities.append((recipe.pk, run.pk, cell.pk))
            executor = MigrationExecutor(database)
            after = [("overbae", "0032_datasetpipelinebinding_datasetpipelinepackage_and_more")]
            executor.migrate(after)
            current = executor.loader.project_state(after).apps
            requests, attempts = set(), set()
            for recipe_id, run_id, cell_id in identities:
                recipe = current.get_model("overbae", "DatasetPipeline").objects.get(pk=recipe_id)
                run = current.get_model("overbae", "DatasetPipelineRun").objects.get(pk=run_id)
                assert (
                    recipe.project_id == project.pk
                    and recipe.family == recipe.pk
                    and recipe.revision == 1
                )
                assert (
                    run.output_id == cell_id
                    and run.result == {"rows": 10000}
                    and run.state == "completed"
                )
                requests.add(recipe.request_key)
                attempts.add(run.attempt)
            assert len(requests) == len(attempts) == 2
        finally:
            database.close()
            connections["default"] = original
