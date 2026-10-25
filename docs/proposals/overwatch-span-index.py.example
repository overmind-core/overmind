from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("overbae", "0052_overwatch_posthog_funnel")]

    operations = [
        AddIndexConcurrently(
            model_name="span",
            index=models.Index(fields=["project", "received_at"], name="span_project_received"),
        ),
    ]
