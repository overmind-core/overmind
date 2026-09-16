from django.db import migrations, models


def mark_existing_mapping_confirmed(apps, schema_editor):
    ConnectorCredential = apps.get_model("overbae", "ConnectorCredential")
    ConnectorCredential.objects.all().update(capability_mapping_confirmed=True)


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0002_workshop_engines"),
    ]

    operations = [
        migrations.AddField(
            model_name="connectorcredential",
            name="pending_capability_mapping",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="connectorcredential",
            name="capability_mapping_confirmed",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="connectorcredential",
            name="imported_boundary_key",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.RunPython(mark_existing_mapping_confirmed, migrations.RunPython.noop),
    ]
