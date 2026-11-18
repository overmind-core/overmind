import django.db.models.deletion
from django.db import migrations, models

CATEGORY_KIND = {
    "bug": "failure",
    "improvement": "idea",
    "question": "question",
    "love": "praise",
    "feature": "missing",
    "performance": "stuck",
    "accessibility": "failure",
    "security": "failure",
    "documentation": "confusing",
    "other": "idea",
}
KIND_CATEGORY = {
    "failure": "bug",
    "mismatch": "bug",
    "confusing": "documentation",
    "missing": "feature",
    "stuck": "performance",
    "dead_end": "bug",
    "idea": "improvement",
    "praise": "love",
    "question": "question",
}


def title(message):
    line = next((line.strip() for line in message.splitlines() if line.strip()), "Feedback")
    return line if len(line) <= 120 else line[:119] + "…"


def to_reports(apps, schema_editor):
    Feedback = apps.get_model("overbae", "Feedback")
    for row in Feedback.objects.all().only("id", "kind", "message"):
        row.kind = CATEGORY_KIND.get(row.kind, "idea")
        row.title = title(row.message)
        row.save(update_fields=["kind", "title"])


def to_categories(apps, schema_editor):
    Feedback = apps.get_model("overbae", "Feedback")
    for row in Feedback.objects.all().only("id", "kind"):
        row.kind = KIND_CATEGORY.get(row.kind, "other")
        row.save(update_fields=["kind"])


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0059_data_plane_rebuild"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="agent_feedback",
            field=models.BooleanField(blank=True, default=None, null=True),
        ),
        migrations.AddField(
            model_name="user",
            name="agent_feedback_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RenameField(model_name="feedback", old_name="feedback", new_name="message"),
        migrations.RenameField(model_name="feedback", old_name="category", new_name="kind"),
        migrations.AddField(
            model_name="feedback",
            name="title",
            field=models.CharField(default="", max_length=160),
            preserve_default=False,
        ),
        migrations.RunPython(to_reports, to_categories),
        migrations.AlterField(
            model_name="feedback",
            name="kind",
            field=models.CharField(
                choices=[
                    ("failure", "Something failed"),
                    ("mismatch", "Behaviour differs from its description"),
                    ("confusing", "Unclear guidance"),
                    ("missing", "Missing capability"),
                    ("stuck", "Slow or stuck work"),
                    ("dead_end", "No progress after repeated attempts"),
                    ("idea", "Idea"),
                    ("praise", "Worked well"),
                    ("question", "Question"),
                ],
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="feedback",
            name="source",
            field=models.CharField(
                choices=[("console", "Console"), ("agent", "Coding agent")],
                default="console",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="feedback",
            name="project",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="feedback",
                to="overbae.project",
            ),
        ),
        migrations.AddField(
            model_name="feedback",
            name="area",
            field=models.CharField(
                choices=[
                    ("setup", "Setup and connection"),
                    ("observability", "Observability"),
                    ("datasets", "Datasets"),
                    ("evaluation", "Evaluation"),
                    ("optimizer", "Optimiser"),
                    ("training", "Training"),
                    ("serving", "Serving"),
                    ("billing", "Billing"),
                    ("other", "Other"),
                ],
                default="other",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="feedback",
            name="severity",
            field=models.CharField(
                blank=True,
                choices=[("blocked", "Blocked"), ("degraded", "Degraded"), ("minor", "Minor")],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="feedback",
            name="outcome",
            field=models.CharField(
                blank=True,
                choices=[
                    ("abandoned", "Abandoned"),
                    ("worked_around", "Worked around"),
                    ("resolved", "Resolved after retries"),
                    ("handed_to_user", "Handed to the user"),
                ],
                default="",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="feedback",
            name="detail",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="feedback",
            name="environment",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="feedback",
            name="fingerprint",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="feedback",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]
