from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0051_overwatch_funnel_delivery"),
    ]

    operations = [
        migrations.AddField(
            model_name="overwatchfunneldelivery",
            name="posthog_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="overwatchfunneldelivery",
            name="posthog_delivered_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="overwatchfunneldelivery",
            name="posthog_last_error",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
    ]
