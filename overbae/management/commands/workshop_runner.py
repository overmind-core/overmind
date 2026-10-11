import time

from django.core.management.base import BaseCommand

from overbae.services.datasets import pipeline_runner


class Command(BaseCommand):
    help = "Run the dedicated isolated Workshop execution controller."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        with pipeline_runner.controller() as owner:
            while True:
                with owner.cursor() as cursor:
                    cursor.execute("SELECT 1")
                pipeline_runner.tick()
                if options["once"]:
                    return
                time.sleep(0.5)
