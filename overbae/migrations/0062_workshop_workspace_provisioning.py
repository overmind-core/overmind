from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0061_workshop_conversation_history")]

    operations = [
        migrations.AlterField(
            model_name="workshopworkspace",
            name="sandbox_id",
            field=models.CharField(max_length=128, unique=True, null=True),
        ),
        migrations.AddField(
            model_name="workshopworkspace",
            name="provisioning",
            field=models.JSONField(default=dict),
        ),
    ]
