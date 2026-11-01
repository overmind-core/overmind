import pytest
from django.db import IntegrityError, transaction
from django.db.migrations.executor import MigrationExecutor
from migration_replay import scratch_database

pytestmark = pytest.mark.covers("migrations")


def test_mcp_receipt_migration_preserves_evaluators_and_enforces_unique_request_keys(
    django_db_blocker,
):
    before = [("overbae", "0055_activity_task_index")]
    after = [("overbae", "0056_mcp_mutations_and_eval_revisions")]
    with scratch_database(django_db_blocker) as database:
        executor = MigrationExecutor(database)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        project = apps.get_model("overbae", "Project").objects.create(
            name="Migration", slug="migration"
        )
        user = apps.get_model("overbae", "User").objects.create(email="migration@test.com")
        row = apps.get_model("overbae", "EvalMatrix").objects.create(
            project=project, name="Existing", type="llm", source="Original definition"
        )
        executor = MigrationExecutor(database)
        executor.migrate(after)
        apps = executor.loader.project_state(after).apps
        assert (
            apps.get_model("overbae", "EvalMatrix").objects.get(pk=row.pk).source
            == "Original definition"
        )
        receipts = apps.get_model("overbae", "MCPMutation")
        payload = dict(
            project_id=project.pk,
            user_id=user.pk,
            tool="create_eval_matrix_row",
            request_key="same-key",
            fingerprint="a" * 64,
        )
        receipts.objects.create(**payload)
        with pytest.raises(IntegrityError), transaction.atomic():
            receipts.objects.create(**payload)
        revision = apps.get_model("overbae", "EvalMatrixRevision").objects.create(
            row_id=row.pk, definition={"source": "Original definition"}, created_by_id=user.pk
        )
        assert revision.definition["source"] == "Original definition"
        executor = MigrationExecutor(database)
        executor.migrate(before)
        assert (
            executor.loader.project_state(before)
            .apps.get_model("overbae", "EvalMatrix")
            .objects.get(pk=row.pk)
            .source
            == "Original definition"
        )
