from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0058_workshop_usage_tracking")]

    operations = [
        migrations.AddField(
            model_name="workshoptask",
            name="context_sources",
            field=models.JSONField(default=list),
        ),
    ]
