import time

from django.core.management.base import BaseCommand

from overbae.services.datasets import pipeline_runner


class Command(BaseCommand):
    help = "Run the dedicated isolated Workshop execution controller."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true")

    def handle(self, *args, **options):
        while True:
            pipeline_runner.tick()
            if options["once"]:
                return
            time.sleep(0.5)
