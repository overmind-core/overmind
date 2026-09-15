"""Credit gate for user-initiated paid work.

Binary: any positive balance passes, whatever the job costs. ``completions.py``
gates separately to answer in OpenAI's error shape; background work (webhooks,
cron, OTLP ingest, reconcilers) is never gated.
"""

from rest_framework.exceptions import APIException

from overbae.services.billing_ledger import InsufficientCredits, ensure_credits

__all__ = ["PaymentRequired", "require_credits"]


class PaymentRequired(APIException):
    status_code = 402
    default_detail = "Insufficient credits."
    default_code = "insufficient_credits"


def require_credits(user) -> None:
    try:
        ensure_credits(user)
    except InsufficientCredits as exc:
        raise PaymentRequired() from exc
