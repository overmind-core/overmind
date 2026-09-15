from django.db import migrations, models


def relabel_workshop_cursor_rows(apps, schema_editor):
    BillingTelemetry = apps.get_model("overbae", "BillingTelemetry")
    rows = BillingTelemetry.objects.filter(service="cursor-agent").only("id", "metadata")
    ids = [
        row.id
        for row in rows.iterator(chunk_size=500)
        if isinstance(row.metadata, dict) and row.metadata.get("dataset_id")
    ]
    for start in range(0, len(ids), 500):
        BillingTelemetry.objects.filter(id__in=ids[start : start + 500]).update(
            service="data-workshop"
        )


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0001_initial_platform"),
    ]

    operations = [
        migrations.RunPython(relabel_workshop_cursor_rows, migrations.RunPython.noop),
        migrations.AddField(
            model_name="dataset",
            name="agent_messages",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="dataset",
            name="agent_turn_key",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
