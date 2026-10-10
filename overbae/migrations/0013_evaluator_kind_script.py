from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0012_dataset_llm_calls"),
    ]

    operations = [
        migrations.AlterField(
            model_name="evaluator",
            name="kind",
            field=models.CharField(
                choices=[
                    ("llm_judge", "Llm Judge"),
                    ("trajectory", "Trajectory"),
                    ("deterministic", "Deterministic"),
                    ("statistical", "Statistical"),
                    ("agentic", "Agentic"),
                    ("script", "Script"),
                ],
                max_length=20,
            ),
        ),
    ]
