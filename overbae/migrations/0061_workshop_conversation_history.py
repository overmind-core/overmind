from django.db import migrations


def retain_history(apps, schema_editor):
    Task = apps.get_model("overbae", "WorkshopTask")
    Event = apps.get_model("overbae", "WorkshopChatEvent")
    Cell = apps.get_model("overbae", "Cell")
    for task in Task.objects.order_by("created_at").iterator():
        Event.objects.get_or_create(
            task=task,
            key="objective",
            defaults={
                "message": {
                    "id": "objective",
                    "role": "user",
                    "text": task.objective,
                    "status": "completed",
                }
            },
        )
        if task.result.get("reason"):
            Event.objects.get_or_create(
                task=task,
                key="retained-result",
                defaults={
                    "message": {
                        "id": "retained-result",
                        "role": "assistant",
                        "text": task.result["reason"],
                        "status": "completed",
                    }
                },
            )
        cells = [
            {
                "id": str(cell.pk),
                "dataset": str(cell.dataset_id),
                "name": cell.title,
                "rows": cell.rows,
                "columns": cell.columns,
                "checks": cell.intent_report,
            }
            for cell in Cell.objects.filter(
                pk__in=task.result.get("outputs", []), dataset__project_id=task.project_id
            ).order_by("position")
        ]
        if cells:
            Event.objects.get_or_create(
                task=task,
                key="retained-publication",
                defaults={
                    "message": {
                        "id": "retained-publication",
                        "role": "publication",
                        "text": "Published cells",
                        "status": "completed",
                        "cells": cells,
                    }
                },
            )


class Migration(migrations.Migration):
    dependencies = [("overbae", "0060_workshop_conversation")]
    operations = [migrations.RunPython(retain_history, migrations.RunPython.noop)]
