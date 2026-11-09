from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0062_workshop_workspace_provisioning")]
    operations = [
        migrations.AddField(
            model_name="workshoptask", name="retention", field=models.JSONField(default=dict)
        )
    ]
