from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0057_workshop_agent")]

    operations = [
        migrations.RemoveField(model_name="workshoptask", name="max_tokens"),
        migrations.AlterField(
            model_name="workshoptask",
            name="max_steps",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
