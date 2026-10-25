import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0050_remove_user_projects_limit"),
    ]

    operations = [
        migrations.CreateModel(
            name="OverwatchFunnelDelivery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "step",
                    models.CharField(
                        choices=[
                            ("signed_up", "Signed up"),
                            ("joined_project", "Joined a project"),
                            ("sent_first_span", "Sent first trace"),
                            ("synced_capabilities", "Synced capabilities"),
                            ("ran_evaluation", "Ran evaluation"),
                            ("started_training", "Started training"),
                            ("deployed_model", "Deployed model"),
                            ("called_alias", "Called model alias"),
                        ],
                        max_length=32,
                    ),
                ),
                ("source_at", models.DateTimeField(db_index=True)),
                ("channel_id", models.CharField(max_length=32)),
                ("status", models.CharField(choices=[("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed")], db_index=True, default="pending", max_length=12)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("delivered_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.CharField(blank=True, default="", max_length=160)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="overwatch_funnel_deliveries", to="overbae.user")),
            ],
            options={
                "indexes": [models.Index(fields=["status", "source_at"], name="overbae_ove_status_3f53a2_idx")],
                "constraints": [models.UniqueConstraint(fields=("user", "step"), name="overwatch_funnel_user_step_unique")],
            },
        ),
    ]
