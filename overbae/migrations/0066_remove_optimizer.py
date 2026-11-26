from django.db import migrations, models


def optimizer_feedback_to_other(apps, schema_editor):
    Feedback = apps.get_model("overbae", "Feedback")
    Feedback.objects.filter(area="optimizer").update(area="other")


def retire_optimizer_reconciler(apps, schema_editor):
    # The database scheduler keeps a row for every entry it ever loaded from settings.
    PeriodicTask = apps.get_model("django_celery_beat", "PeriodicTask")
    PeriodicTask.objects.using(schema_editor.connection.alias).filter(
        task="overbae.tasks.optimizer_reconciler.reconcile_optimizer_experiments"
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0065_job_leases"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(optimizer_feedback_to_other, migrations.RunPython.noop),
        migrations.RunPython(retire_optimizer_reconciler, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="feedback",
            name="area",
            field=models.CharField(
                choices=[
                    ("setup", "Setup and connection"),
                    ("observability", "Observability"),
                    ("datasets", "Datasets"),
                    ("evaluation", "Evaluation"),
                    ("training", "Training"),
                    ("serving", "Serving"),
                    ("billing", "Billing"),
                    ("other", "Other"),
                ],
                default="other",
                max_length=16,
            ),
        ),
        migrations.DeleteModel(name="OptimizerCommand"),
        migrations.DeleteModel(name="OptimizerCandidate"),
        migrations.DeleteModel(name="OptimizerIteration"),
        migrations.DeleteModel(name="OptimizerExperiment"),
        migrations.RemoveField(model_name="capability", name="analyzer_model"),
        migrations.RemoveField(model_name="capability", name="consistency_rules"),
        migrations.RemoveField(model_name="capability", name="fixed_elements"),
        migrations.RemoveField(model_name="capability", name="optimizable_elements"),
        migrations.RemoveField(model_name="capability", name="structure_weight"),
        migrations.RemoveField(model_name="capability", name="tool_usage_weight"),
        migrations.RemoveField(model_name="capability", name="total_points"),
    ]
