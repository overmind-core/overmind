from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0005_workshop_training")]

    operations = [
        migrations.AddField(
            model_name="finetuningjob",
            name="eval_judge_model",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
