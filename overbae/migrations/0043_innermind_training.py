"""Join the committed evaluation and training histories without rewriting either."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0016_innermind"),
        ("overbae", "0042_trainingplan"),
    ]

    operations = []
