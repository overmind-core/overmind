from django.core.management.base import BaseCommand

from overbae.services.workspaces import daytona


class Command(BaseCommand):
    help = "Build the Daytona snapshot that project workspaces start from."

    def handle(self, *args, **options):
        self.stdout.write(daytona.build_snapshot(self.stdout.write))
