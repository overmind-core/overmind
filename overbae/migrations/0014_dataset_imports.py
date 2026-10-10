import uuid

import django.contrib.postgres.indexes
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0013_eval_generation_admission")]

    operations = [
        migrations.CreateModel(
            name="DatasetImport",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("queued", "Queued"),
                            ("running", "Running"),
                            ("blocked", "Blocked"),
                            ("complete", "Complete"),
                            ("cancelled", "Cancelled"),
                        ],
                        default="queued",
                        max_length=16,
                    ),
                ),
                ("inputs", models.JSONField(default=dict)),
                ("source_manifest", models.JSONField(default=list)),
                ("queued_at", models.DateTimeField()),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("published_at", models.DateTimeField(blank=True, null=True)),
                ("next_publish_at", models.DateTimeField(blank=True, null=True)),
                ("lease_until", models.DateTimeField(blank=True, null=True)),
                ("owner", models.UUIDField(blank=True, null=True)),
                ("attempts", models.PositiveIntegerField(default=0)),
                ("publish_attempts", models.PositiveIntegerField(default=0)),
                ("publish_owner", models.UUIDField(null=True, blank=True)),
                ("failure_code", models.CharField(blank=True, default="", max_length=64)),
                ("error", models.TextField(blank=True, default="")),
                ("result", models.JSONField(blank=True, default=dict)),
                ("handoff_pending", models.BooleanField(default=False)),
                ("handoff_owner", models.UUIDField(null=True, blank=True)),
                ("handoff_lease_until", models.DateTimeField(null=True, blank=True)),
                ("handoff_attempts", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "dataset",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="import_run",
                        to="overbae.dataset",
                    ),
                ),
                (
                    "evaluation",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="split_imports",
                        to="overbae.dataset",
                    ),
                ),
            ],
            options={
                "indexes": [
                    models.Index(
                        fields=["state", "next_publish_at"], name="dataset_import_publish"
                    ),
                    models.Index(fields=["state", "queued_at"], name="dataset_import_queue"),
                    models.Index(fields=["state", "lease_until"], name="dataset_import_lease"),
                    django.contrib.postgres.indexes.GinIndex(
                        fields=["source_manifest"], name="dataset_import_sources"
                    ),
                    models.Index(
                        fields=["handoff_lease_until", "queued_at"],
                        name="dataset_import_handoff",
                        condition=models.Q(handoff_pending=True, state="complete"),
                    ),
                ]
            },
        ),
    ]
