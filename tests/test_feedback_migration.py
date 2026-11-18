import pytest
from django.db.migrations.executor import MigrationExecutor
from migration_replay import scratch_database

pytestmark = pytest.mark.covers("migrations")


def test_console_feedback_keeps_its_message_and_gains_a_kind_and_title(django_db_blocker):
    before = [("overbae", "0059_data_plane_rebuild")]
    after = [("overbae", "0060_feedback_reports")]
    with scratch_database(django_db_blocker) as database:
        executor = MigrationExecutor(database)
        executor.migrate(before)
        old = executor.loader.project_state(before).apps.get_model("overbae", "Feedback")
        love = old.objects.create(category="love", feedback="\n  Traces caught it.\nThanks")
        docs = old.objects.create(category="documentation", feedback="x" * 200)
        executor = MigrationExecutor(database)
        executor.migrate(after)
        new = executor.loader.project_state(after).apps.get_model("overbae", "Feedback")
        love, docs = new.objects.get(pk=love.pk), new.objects.get(pk=docs.pk)
        assert (love.kind, love.title, love.message) == (
            "praise",
            "Traces caught it.",
            "\n  Traces caught it.\nThanks",
        )
        assert (docs.kind, len(docs.title), docs.source, docs.area) == (
            "confusing",
            120,
            "console",
            "other",
        )
        executor = MigrationExecutor(database)
        executor.migrate(before)
        rolled = executor.loader.project_state(before).apps.get_model("overbae", "Feedback")
        assert rolled.objects.get(pk=love.pk).category == "love"
