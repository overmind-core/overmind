import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0012_dataset_llm_calls")]

    operations = [
        migrations.AddIndex(
            model_name="evalrun",
            index=models.Index(
                fields=["project", "id"],
                condition=models.Q(status="running"),
                name="eval_running_project",
            ),
        ),
        migrations.CreateModel(
            name="EvalGenerationScheduler",
            fields=[
                (
                    "id",
                    models.PositiveSmallIntegerField(
                        default=1, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("last_tick_at", models.DateTimeField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name="EvalGenerationRun",
            fields=[
                (
                    "run",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        serialize=False,
                        to="overbae.evalrun",
                    ),
                ),
                ("last_admitted_at", models.DateTimeField(null=True)),
                ("scoring_task_id", models.CharField(blank=True, max_length=255)),
                ("scoring_started_at", models.DateTimeField(null=True)),
            ],
        ),
        migrations.CreateModel(
            name="EvalGenerationWork",
            fields=[
                (
                    "sample",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        primary_key=True,
                        serialize=False,
                        to="overbae.evalsample",
                    ),
                ),
                ("state", models.CharField(db_index=True, default="waiting", max_length=16)),
                ("task_id", models.CharField(blank=True, max_length=255)),
                ("queued_at", models.DateTimeField(null=True)),
                ("started_at", models.DateTimeField(null=True)),
                ("finished_at", models.DateTimeField(null=True)),
                ("expires_at", models.DateTimeField(null=True)),
            ],
            options={
                "indexes": [
                    models.Index(fields=["state", "expires_at"], name="eval_gen_state_expiry")
                ]
            },
        ),
    ]
