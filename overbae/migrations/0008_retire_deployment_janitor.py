from django.db import migrations


def retire_janitor(apps, schema_editor):
    periodic_task = apps.get_model("django_celery_beat", "PeriodicTask")
    periodic_task.objects.using(schema_editor.connection.alias).filter(
        task="overbae.tasks.inference_controller.janitor_stuck_fsm",
    ).update(enabled=False)


class Migration(migrations.Migration):
    dependencies = [
        ("overbae", "0007_resumable_deployments"),
        ("django_celery_beat", "0001_initial"),
    ]

    operations = [migrations.RunPython(retire_janitor, migrations.RunPython.noop)]
