from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0008_retire_deployment_janitor")]

    operations = [
        migrations.AddField(
            model_name="cell", name="review", field=models.JSONField(blank=True, default=dict)
        ),
        migrations.AddField(
            model_name="cell",
            name="quality_report",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
