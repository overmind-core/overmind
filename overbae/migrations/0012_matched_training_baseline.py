from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0011_pin_training_eval_version")]

    operations = [
        migrations.AlterField(
            model_name="finetuningjob",
            name="eval_incumbent_before",
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name="finetuningjob",
            name="eval_model_before",
            field=models.BooleanField(default=True),
        ),
    ]
