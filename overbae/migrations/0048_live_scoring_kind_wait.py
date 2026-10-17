from django.contrib.postgres.operations import AddIndexConcurrently, RemoveIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("overbae", "0047_landing_admission")]

    operations = [
        AddIndexConcurrently(
            model_name="livescoringwork",
            index=models.Index(
                fields=["project", "state", "kind", "id"], name="live_scoring_kind_wait"
            ),
        ),
        RemoveIndexConcurrently(model_name="livescoringwork", name="live_scoring_project_wait"),
    ]
