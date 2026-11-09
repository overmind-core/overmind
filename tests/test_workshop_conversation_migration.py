import pytest
from django.db.migrations.executor import MigrationExecutor
from migration_replay import scratch_database

pytestmark = pytest.mark.covers("migrations")


def test_existing_task_history_survives_conversation_migration(django_db_blocker):
    before = [("overbae", "0059_workshop_context_sources")]
    after = [("overbae", "0061_workshop_conversation_history")]
    with scratch_database(django_db_blocker) as database:
        executor = MigrationExecutor(database)
        executor.migrate(before)
        apps = executor.loader.project_state(before).apps
        project = apps.get_model("overbae", "Project").objects.create(
            name="Migration", slug="migration"
        )
        user = apps.get_model("overbae", "User").objects.create(email="conversation@test.com")
        dataset = apps.get_model("overbae", "Dataset").objects.create(project=project)
        task = apps.get_model("overbae", "WorkshopTask").objects.create(
            project=project,
            dataset=dataset,
            created_by=user,
            request_key="original",
            objective="Inspect the data",
            state="completed",
            result={"reason": "Three records inspected."},
        )
        executor = MigrationExecutor(database)
        executor.migrate(after)
        events = executor.loader.project_state(after).apps.get_model("overbae", "WorkshopChatEvent")
        assert list(
            events.objects.filter(task_id=task.pk)
            .order_by("pk")
            .values_list("message__text", flat=True)
        ) == ["Inspect the data", "Three records inspected."]
