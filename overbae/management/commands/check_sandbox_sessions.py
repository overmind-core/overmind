import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from overbae.services.sandbox_sessions import conformance, provider


class Command(BaseCommand):
    help = "Run the session contract against the configured provider, as it is deployed."

    def handle(self, *args, **options):
        target = provider()
        failed = 0
        for check in (*conformance.CHECKS, *conformance.ISOLATED_CHECKS):
            started = time.monotonic()
            try:
                check(target)
            except Exception as exc:  # noqa: BLE001 - every check reports, then the command fails
                failed += 1
                outcome = f"FAIL {type(exc).__name__}: {exc}"
            else:
                outcome = "pass"
            seconds = time.monotonic() - started
            self.stdout.write(f"{outcome}  {seconds:6.1f}s  {check.__name__}")
        if failed:
            raise CommandError(f"{settings.SANDBOX_SESSION_PROVIDER}: {failed} checks failed.")
