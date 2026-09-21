import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("overbae", "0012_matched_training_baseline")]

    operations = [
        migrations.AddField(
            model_name="capability",
            name="benchmark_model",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="+",
                to="overbae.deployedmodel",
            ),
        ),
    ]
