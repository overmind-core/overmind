"""Gate the first strict-consumer rollout on completion of unstamped work."""

import json

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q

from overbae.models import Dataset


class Command(BaseCommand):
    help = "Read-only check before replacing pre-clock workshop consumers."

    def add_arguments(self, parser):
        parser.add_argument("--require-idle", action="store_true")

    def handle(self, *args, **options):
        busy = Dataset.objects.filter(state__in=["diagnosing", "running"])
        counts = {
            "busy": busy.count(),
            "unbound": busy.filter(
                Q(workshop_task_id="") | Q(workshop_queued_at__isnull=True)
            ).count(),
        }
        self.stdout.write(json.dumps(counts))
        if counts["unbound"] or (options["require_idle"] and counts["busy"]):
            raise CommandError("Keep the existing consumers running until their work finishes.")
