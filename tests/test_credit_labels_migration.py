from decimal import Decimal

import pytest
from django.db.migrations.executor import MigrationExecutor
from django.db.models import Sum
from migration_replay import scratch_database

pytestmark = pytest.mark.covers("migrations")


def test_only_live_checkout_rows_become_purchases_and_balances_hold(django_db_blocker):
    before = [("overbae", "0067_delete_retired_beat_tasks")]
    after = [("overbae", "0068_credits_only")]
    rows = [
        ("free-credits", "50", "free-credits:7", "grant", "signup:7"),
        ("stripe-topup", "25", "topup:cs_live_a1", "purchase", "topup:cs_live_a1"),
        ("stripe-topup", "1000", "topup:cs_test_a1", "grant", "topup:cs_test_a1"),
        ("stripe-topup", "120", "in_1Pro", "grant", "in_1Pro"),
        ("stripe-topup", "200", "signup-promo:7", "grant", "signup-promo:7"),
        ("stripe-topup", "-490", "signup-promo-reset:7", "grant", "signup-promo-reset:7"),
        ("inference", "-3", "inf-1", "inference", "inf-1"),
    ]
    with scratch_database(django_db_blocker) as database:
        executor = MigrationExecutor(database)
        executor.migrate(before)
        state = executor.loader.project_state(before).apps
        user = state.get_model("overbae", "User").objects.create(email="ledger@example.com")
        old = state.get_model("overbae", "BillingTelemetry")
        for service, amount, key, _, _ in rows:
            old.objects.create(user=user, service=service, amount=amount, idempotency_key=key)
        executor = MigrationExecutor(database)
        executor.migrate(after)
        new = executor.loader.project_state(after).apps.get_model("overbae", "BillingTelemetry")

        labelled = sorted(new.objects.values_list("service", "idempotency_key"))
        assert labelled == sorted((service, key) for _, _, _, service, key in rows)
        assert new.objects.aggregate(total=Sum("amount"))["total"] == Decimal("902")
