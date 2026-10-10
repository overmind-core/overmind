"""Check durable Workshop pipeline ownership before replacing consumers."""

import json

from django.core.management.base import BaseCommand, CommandError

from overbae.models import DatasetPipelineRun


class Command(BaseCommand):
    help = "Read-only check of active Workshop execution receipts."

    def add_arguments(self, parser):
        parser.add_argument("--require-idle", action="store_true")

    def handle(self, *args, **options):
        busy = DatasetPipelineRun.objects.filter(state__in=["queued", "running"])
        counts = {
            "busy": busy.count(),
            "unbound": busy.filter(state="running", lease_until__isnull=True).count(),
        }
        self.stdout.write(json.dumps(counts))
        if counts["unbound"] or (options["require_idle"] and counts["busy"]):
            raise CommandError("Keep the existing consumers running until their work finishes.")
