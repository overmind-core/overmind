from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0012_dataset_llm_calls")]

    operations = [
        migrations.AddField(
            model_name="dataset",
            name="preparation_plan",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="cell",
            name="preparation_plan",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
