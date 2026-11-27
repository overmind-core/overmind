"""Set the Stripe webhook endpoint's events to the ones credit purchases need.

Run it once per environment after a deploy that changes ``WEBHOOK_EVENTS``:
``manage.py sync_stripe_webhook --url https://<api host>/api/billing/webhook/``.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from overbae.services.credit_purchases import configure_stripe, sync_webhook_endpoint


class Command(BaseCommand):
    help = "Set the Stripe webhook endpoint's events to the credit purchase events."

    def add_arguments(self, parser):
        parser.add_argument("--url", required=True, help="The endpoint URL registered in Stripe.")

    def handle(self, *args, **options):
        if not configure_stripe():
            raise CommandError("STRIPE_SECRET_KEY is not set.")
        try:
            events = sync_webhook_endpoint(options["url"])
        except LookupError as exc:
            raise CommandError(str(exc)) from exc
        self.stdout.write(f"{options['url']}: {', '.join(events)}")
