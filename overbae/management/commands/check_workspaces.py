import time

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from overbae.services.workspaces import conformance, provider


class Command(BaseCommand):
    help = "Run the workspace contract against the configured provider, as it is deployed."

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
            self.stdout.write(f"{outcome}  {time.monotonic() - started:6.1f}s  {check.__name__}")
        if failed:
            raise CommandError(f"{settings.WORKSPACE_PROVIDER}: {failed} checks failed.")
