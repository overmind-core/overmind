from django.db import migrations
from django.db.models import Q


def archive_ownership(apps, schema_editor):
    alias = schema_editor.connection.alias
    Dataset = apps.get_model("overbae", "Dataset")
    History = apps.get_model("overbae", "DatasetHistory")
    Import = apps.get_model("overbae", "DatasetImport")
    for dataset in (
        Dataset.objects.using(alias)
        .filter(
            ~Q(workshop_task_id="")
            | Q(workshop_queued_at__isnull=False)
            | Q(workshop_started_at__isnull=False)
        )
        .iterator()
    ):
        History.objects.using(alias).create(
            dataset_id=dataset.pk,
            kind="workshop_ownership",
            reference=dataset.workshop_task_id,
            payload={
                "task_id": dataset.workshop_task_id,
                "queued_at": dataset.workshop_queued_at.isoformat()
                if dataset.workshop_queued_at
                else None,
                "started_at": dataset.workshop_started_at.isoformat()
                if dataset.workshop_started_at
                else None,
            },
        )
    for run in (
        Import.objects.using(alias)
        .filter(
            Q(handoff_attempts__gt=0)
            | Q(handoff_pending=True)
            | Q(handoff_owner__isnull=False)
            | Q(handoff_lease_until__isnull=False)
        )
        .iterator()
    ):
        History.objects.using(alias).create(
            dataset_id=run.dataset_id,
            kind="import_handoff",
            reference=str(run.pk),
            payload={
                "pending": run.handoff_pending,
                "attempts": run.handoff_attempts,
                "owner": str(run.handoff_owner) if run.handoff_owner else None,
                "lease_until": run.handoff_lease_until.isoformat()
                if run.handoff_lease_until
                else None,
                "result": run.result,
            },
        )


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0033_training_monitoring"),
        ("overbae", "0015_workshop_execution_clock"),
    ]

    operations = [
        migrations.RunPython(archive_ownership, migrations.RunPython.noop),
        migrations.RemoveIndex("dataset", "dataset_workshop_busy_idx"),
        migrations.RemoveField("dataset", "workshop_task_id"),
        migrations.RemoveField("dataset", "workshop_queued_at"),
        migrations.RemoveField("dataset", "workshop_started_at"),
        migrations.RemoveIndex("datasetimport", "dataset_import_handoff"),
        migrations.RemoveField("datasetimport", "handoff_pending"),
        migrations.RemoveField("datasetimport", "handoff_owner"),
        migrations.RemoveField("datasetimport", "handoff_lease_until"),
        migrations.RemoveField("datasetimport", "handoff_attempts"),
    ]
