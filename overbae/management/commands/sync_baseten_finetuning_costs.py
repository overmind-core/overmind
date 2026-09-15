from __future__ import annotations

from datetime import UTC, datetime

from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from overbae.services.baseten_billing import EARLIEST_QUERYABLE, sync_finetuning_job_costs


def _parse_dt(raw: str | None, *, default: datetime) -> datetime:
    if not raw:
        return default
    dt = parse_datetime(raw)
    if dt is None:
        # Allow date-only YYYY-MM-DD
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError as exc:
            raise CommandError(f"Invalid datetime: {raw}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


class Command(BaseCommand):
    help = "Sync Baseten training costs onto FinetuningJob rows from the billing API."

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            help="ISO start (default: 2026-01-01 UTC, Baseten earliest queryable date)",
        )
        parser.add_argument(
            "--until",
            help="ISO end (default: now)",
        )

    def handle(self, *args, **options):
        since = _parse_dt(options.get("since"), default=EARLIEST_QUERYABLE)
        until = _parse_dt(options.get("until"), default=timezone.now())
        if until <= since:
            raise CommandError("--until must be after --since")
        result = sync_finetuning_job_costs(since=since, until=until)
        self.stdout.write(
            self.style.SUCCESS(
                f"chunks={result['chunks']} fetched={result['fetched']} "
                f"matched={result['matched']} updated={result['updated']}"
            )
        )
