import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0015_workshop_execution_clock"),
    ]

    operations = [
        migrations.CreateModel(
            name="DecisionJob",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("model_id", models.CharField(max_length=255)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("succeeded", "Succeeded"),
                            ("failed", "Failed"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("records", models.JSONField(default=list)),
                ("remote_id", models.CharField(blank=True, default="", max_length=255)),
                ("result", models.JSONField(default=dict)),
                ("error", models.TextField(blank=True, default="")),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "project",
                    models.ForeignKey(
                        on_delete=models.deletion.CASCADE,
                        related_name="decision_jobs",
                        to="overbae.project",
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
