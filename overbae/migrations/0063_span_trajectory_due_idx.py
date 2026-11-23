from django.contrib.postgres.operations import AddIndexConcurrently
from django.db import migrations, models


class Migration(migrations.Migration):
    atomic = False

    dependencies = [("overbae", "0062_live_scoring_waiting_since")]

    operations = [
        AddIndexConcurrently(
            model_name="span",
            index=models.Index(
                fields=["received_at"],
                condition=models.Q(parent_span_id__isnull=True, eval_attributes__has_key="score")
                & ~models.Q(eval_attributes__has_key="trajectory"),
                name="span_trajectory_due_idx",
            ),
        ),
    ]
