import os

from daytona import Daytona, DaytonaConfig
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from overbae.models import Project, WorkshopWorkspace


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--project", required=True)
        parser.add_argument("--sandbox", required=True)

    def handle(self, *args, **options):
        if not os.environ.get("DAYTONA_API_KEY"):
            raise CommandError("DAYTONA_API_KEY must be configured.")
        project = Project.objects.get(pk=options["project"], is_active=True)
        client = Daytona(
            DaytonaConfig(
                api_key=os.environ["DAYTONA_API_KEY"],
                api_url="https://app.daytona.io/api",
                otel_enabled=False,
            )
        )
        sandbox = client.get(options["sandbox"], request_timeout=30)
        if (
            sandbox.state != "started"
            or sandbox.auto_stop_interval != 0
            or sandbox.auto_pause_interval not in (None, 0)
            or sandbox.auto_delete_interval != -1
            or sandbox.auto_destroy_at
            or not sandbox.network_block_all
        ):
            raise CommandError(
                "Workspace must be running, private-network isolated, and configured without automatic shutdown or deletion."
            )
        if sandbox.public:
            raise CommandError("Workspace must be private.")
        with transaction.atomic():
            Project.objects.select_for_update().get(pk=project.pk)
            existing = WorkshopWorkspace.objects.filter(project=project).first()
            if existing and existing.sandbox_id != sandbox.id:
                raise CommandError(
                    "Workspace replacement requires an explicit checkpoint and recovery procedure."
                )
            WorkshopWorkspace.objects.get_or_create(
                project=project, defaults={"sandbox_id": sandbox.id}
            )
        self.stdout.write(f"Workspace {sandbox.id} is bound to project {project.pk}.")
