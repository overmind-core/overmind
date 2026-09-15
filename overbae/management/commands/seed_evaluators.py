"""Seed the global managed evaluator templates. Idempotent: versions bump only
when content changes.
"""

from django.core.management.base import BaseCommand

from overbae.services.eval.managed import upsert_managed_evaluators


class Command(BaseCommand):
    help = "Create/update the platform's managed evaluator templates."

    def handle(self, *args, **options):
        result = upsert_managed_evaluators()
        self.stdout.write(
            self.style.SUCCESS(
                f"Managed evaluators upserted: {result['created']} created, {result['updated']} updated."
            )
        )
