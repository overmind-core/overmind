from decimal import Decimal

import pytest
from django.db import connections
from django.db.backends.postgresql.base import DatabaseWrapper
from django.db.migrations.executor import MigrationExecutor
from django.test import override_settings
from test_workshop_training_migration import scratch_database as scratch_database

from overbae.models import OptimizerExperiment
from overbae.services.optimizer_ledger import complete_experiment

BEFORE = [("overbae", "0034_source_import_recovery")]
AFTER = [("overbae", "0035_retire_cursor_usage")]


@pytest.mark.parametrize("conflicting_archive", [False, True])
def test_retiring_usage_preserves_experiments_and_ledger(
    scratch_database, django_db_blocker, conflicting_archive
):
    original = connections["default"]
    database = DatabaseWrapper(
        {**original.settings_dict, "NAME": scratch_database}, alias="default"
    )
    with django_db_blocker.unblock(), override_settings(MIGRATION_MODULES={}):
        connections["default"] = database
        try:
            executor = MigrationExecutor(database)
            executor.migrate(BEFORE)
            apps = executor.loader.project_state(BEFORE).apps
            project = apps.get_model("overbae", "Project").objects.create(name="Retained")
            capability = apps.get_model("overbae", "Capability").objects.create(
                project=project, name="Retained", slug="retained"
            )
            user = apps.get_model("overbae", "User").objects.create(email="old@example.test")
            experiments = apps.get_model("overbae", "OptimizerExperiment")
            usage = {"input_tokens": 9007199254740993, "output_tokens": 20, "unknown": [0, None]}
            state = {"eval_pending": {}, "winner_score": 42}
            if conflicting_archive:
                state["archived_cursor_usage"] = {"separate": "retained evidence"}
            experiment = experiments.objects.create(
                project=project,
                capability=capability,
                triggered_by=user,
                state=state,
                cursor_usage=usage,
            )
            empty = experiments.objects.create(
                project=project, capability=capability, state={"keep": True}
            )
            ledger = apps.get_model("overbae", "BillingTelemetry")
            receipt = ledger.objects.create(
                user=user,
                project=project,
                service="cursor-agent",
                amount=Decimal("-0.55"),
                idempotency_key=f"cursor-agent:optimizer:{experiment.pk}",
                metadata={"llm_usage": {"served_model": "composer-2.5"}},
            )
            before_receipts = list(ledger.objects.values())

            executor = MigrationExecutor(database)
            if conflicting_archive:
                with pytest.raises(RuntimeError, match="archive already exists"):
                    executor.migrate(AFTER)
                retained = experiments.objects.get(pk=experiment.pk)
                assert retained.state == state and retained.cursor_usage == usage
                assert list(ledger.objects.values()) == before_receipts
                return

            executor.migrate(AFTER)
            apps = executor.loader.project_state(AFTER).apps
            current = apps.get_model("overbae", "OptimizerExperiment")
            retained = current.objects.get(pk=experiment.pk)
            assert retained.state == {**state, "archived_cursor_usage": usage}
            assert current.objects.get(pk=empty.pk).state == {"keep": True}
            assert retained.updated_at == experiment.updated_at
            with database.cursor() as cursor:
                columns = database.introspection.get_table_description(
                    cursor, "overbae_optimizerexperiment"
                )
            assert "cursor_usage" not in {column.name for column in columns}

            completed = complete_experiment(OptimizerExperiment.objects.get(pk=experiment.pk))
            assert completed.status == "completed"
            assert completed.state["archived_cursor_usage"] == usage
            ledger = apps.get_model("overbae", "BillingTelemetry")
            assert list(ledger.objects.values()) == before_receipts
            assert ledger.objects.get(pk=receipt.pk).amount == Decimal("-0.55")

            executor = MigrationExecutor(database)
            executor.migrate(BEFORE)
            apps = executor.loader.project_state(BEFORE).apps
            restored = apps.get_model("overbae", "OptimizerExperiment").objects.get(
                pk=experiment.pk
            )
            assert restored.cursor_usage == usage
            assert "archived_cursor_usage" not in restored.state
            assert restored.state == {
                key: value
                for key, value in completed.state.items()
                if key != "archived_cursor_usage"
            }
            assert list(ledger.objects.values()) == before_receipts
        finally:
            database.close()
            connections["default"] = original
