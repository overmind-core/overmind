from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0006_finetuningjob_eval_judge_model")]

    operations = [
        migrations.AddField(
            model_name="evalrun",
            name="judge_model",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
