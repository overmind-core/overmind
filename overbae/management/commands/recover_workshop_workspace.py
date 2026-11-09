import os

from daytona import Daytona, DaytonaConfig
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from overbae.models import WorkshopWorkspace
from overbae.services.workshop_agent import lifecycle


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--project", required=True)
        parser.add_argument("--restart", action="store_true")

    def handle(self, *args, **options):
        if not options["restart"]:
            raise CommandError(
                "Pass --restart to terminate uncertain commands; working files remain retained."
            )
        if not os.environ.get("DAYTONA_API_KEY"):
            raise CommandError("DAYTONA_API_KEY must be configured.")
        with transaction.atomic():
            slot = WorkshopWorkspace.objects.select_for_update().get(project_id=options["project"])
            if slot.task_owner:
                task = lifecycle.get(slot.task_owner)
                if task.desired_state == "running":
                    raise CommandError(
                        "Pause or cancel the owning task before restarting its workspace."
                    )
            slot.enabled = False
            slot.save(update_fields=["enabled"])
        client = Daytona(
            DaytonaConfig(
                api_key=os.environ["DAYTONA_API_KEY"],
                api_url="https://app.daytona.io/api",
                otel_enabled=False,
            )
        )
        sandbox = client.get(slot.sandbox_id, request_timeout=30)
        sandbox.stop(timeout=60)
        sandbox.start(timeout=90)
        with transaction.atomic():
            slot = WorkshopWorkspace.objects.select_for_update().get(project_id=options["project"])
            if slot.task_owner:
                task = lifecycle.get(slot.task_owner)
                for action in task.actions.filter(
                    name="execute_python", state__in=["waiting", "unknown", "intent"]
                ):
                    lifecycle.record_action(
                        action.pk,
                        "completed",
                        {
                            "state": "completed",
                            "exit_code": None,
                            "error": "workspace_restarted",
                            "detail": "The operator restarted the workspace. Prior processes have terminated; their exit status is unknown. Inspect retained files before correcting the operation.",
                            "workspace_generation": slot.generation,
                        },
                    )
            slot.generation += 1
            slot.enabled = True
            slot.save(update_fields=["generation", "enabled"])
        self.stdout.write(
            "Workspace restarted; prior command processes terminated. No command was resubmitted."
        )
