"""Backfill PostHog person properties (email, name) for existing registered users."""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from posthog import Posthog

from overbae.models import User


class Command(BaseCommand):
    help = "Backfill email and name onto existing PostHog person profiles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Count eligible users without sending events to PostHog.",
        )

    def handle(self, *args, **options):
        dry_run = bool(options["dry_run"])
        if not dry_run and not settings.POSTHOG_PROJECT_TOKEN:
            raise CommandError("POSTHOG_PROJECT_TOKEN is not configured.")

        users = list(
            User.objects.filter(is_guest=False, is_active=True)
            .exclude(clerk_user_id="")
            .exclude(email="")
            .order_by("id")
        )
        if dry_run:
            self.stdout.write(self.style.SUCCESS(f"dry_run=True users={len(users)}"))
            return

        client = Posthog(
            settings.POSTHOG_PROJECT_TOKEN,
            host=settings.POSTHOG_HOST,
            enable_local_evaluation=False,
        )
        try:
            for user in users:
                traits: dict[str, str] = {
                    "clerkUserId": user.clerk_user_id,
                    "email": user.email,
                    "overmind_user_id": str(user.pk),
                }
                name = user.get_full_name().strip()
                if name:
                    traits["name"] = name
                client.set(distinct_id=user.clerk_user_id, properties=traits)
        finally:
            client.shutdown()

        self.stdout.write(self.style.SUCCESS(f"synced={len(users)}"))
