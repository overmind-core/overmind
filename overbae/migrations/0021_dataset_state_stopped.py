from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0020_workshop_sources_attachments_cancel"),
    ]

    operations = [
        migrations.AlterField(
            model_name="dataset",
            name="state",
            field=models.CharField(
                choices=[
                    ("landing", "Landing"),
                    ("idle", "Idle"),
                    ("error", "Error"),
                    ("stopped", "Stopped"),
                ],
                default="landing",
                max_length=12,
            ),
        ),
    ]
