from django.db import migrations, models


def unsubmitted_jobs_to_modal(apps, schema_editor):
    # The old default stored "baseten" until submission, so these jobs never left Modal's queue.
    FinetuningJob = apps.get_model("overbae", "FinetuningJob")
    FinetuningJob.objects.filter(
        provider="baseten", remote_job_id="", started_at__isnull=True
    ).update(provider="modal")


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0060_feedback_reports"),
    ]

    operations = [
        migrations.AlterField(
            model_name="finetuningjob",
            name="provider",
            field=models.CharField(
                choices=[("nebius", "Nebius"), ("tinker", "Tinker"), ("modal", "Modal")],
                default="modal",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="modelref",
            name="provider",
            field=models.CharField(
                choices=[
                    ("openai", "Openai"),
                    ("anthropic", "Anthropic"),
                    ("gemini", "Gemini"),
                    ("custom", "Custom"),
                ],
                max_length=20,
            ),
        ),
        migrations.RunPython(unsubmitted_jobs_to_modal, migrations.RunPython.noop),
    ]
