"""Grant credits to a user from the shell.

Amounts are in Overmind credits, the unit the UI shows, and are converted to the
USD the ledger stores — 1 credit = $0.01, matching ``usdToOvermindCredits`` in
``frontend/src/lib/utils.ts``. Grants are idempotent by ``--key``, so a retried
or scripted call cannot double a balance; omit it only for one-off grants.
"""

from __future__ import annotations

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError

from overbae.models import BillingService
from overbae.services.billing_ledger import append_entry, balance_usd

USD_PER_CREDIT = Decimal("0.01")


class Command(BaseCommand):
    help = "Grant Overmind credits to a user (1 credit = $0.01)."

    def add_arguments(self, parser):
        parser.add_argument("--email", required=True, help="User's email address.")
        parser.add_argument(
            "--credits",
            required=True,
            type=Decimal,
            help="Credits to grant. Must be positive.",
        )
        parser.add_argument(
            "--key",
            default=None,
            help="Idempotency key. Re-running with the same key grants nothing.",
        )
        parser.add_argument(
            "--reason",
            default="manual-grant",
            help="Stored on the ledger row's metadata for the audit trail.",
        )

    def handle(self, *args, **options):
        credits_granted: Decimal = options["credits"]
        if credits_granted <= 0:
            raise CommandError("--credits must be positive.")

        user_model = get_user_model()
        user = user_model.objects.filter(email__iexact=options["email"]).first()
        if user is None:
            raise CommandError(f"No user with email {options['email']!r}")

        amount_usd = credits_granted * USD_PER_CREDIT
        entry = append_entry(
            user=user,
            amount=amount_usd,
            service=BillingService.STRIPE_TOPUP,
            idempotency_key=options["key"],
            metadata={"granted_by": "grant_credits", "reason": options["reason"]},
        )

        if entry is None:
            self.stdout.write(
                self.style.WARNING(f"Nothing granted — key {options['key']!r} was already used.")
            )
            return

        new_balance = balance_usd(user)
        self.stdout.write(
            self.style.SUCCESS(
                f"Granted {credits_granted:,} credits (${amount_usd}) to {user.email}. "
                f"Balance is now {int(new_balance / USD_PER_CREDIT):,} credits (${new_balance})."
            )
        )
