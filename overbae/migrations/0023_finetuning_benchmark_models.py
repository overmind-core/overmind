from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0022_workshop_execution")]

    operations = [
        migrations.AddField(
            model_name="finetuningjob",
            name="benchmark_models",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AlterField(
            model_name="finetuningjobeval",
            name="kind",
            field=models.CharField(
                choices=[
                    ("baseline", "Baseline"),
                    ("comparator", "Comparator"),
                    ("incumbent_after", "Incumbent After"),
                    ("model_before", "Model Before"),
                    ("checkpoint", "Checkpoint"),
                    ("final", "Final"),
                ],
                max_length=20,
            ),
        ),
    ]
