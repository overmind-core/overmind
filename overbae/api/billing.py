from __future__ import annotations

import logging
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import stripe
from django.conf import settings
from django.db import transaction
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.config import PageNumberPagination
from overbae.api.serializers import (
    BillingTelemetrySerializer,
    CheckoutSessionResponseSerializer,
    CreditTopUpRequestSerializer,
    SubscriptionSerializer,
)
from overbae.models import BillingTelemetry, Subscription, SubscriptionStatus, User
from overbae.services.billing_ledger import (
    CREDITS_PER_USD,
    balance_usd,
    grant_credit_purchase,
    grant_stripe_topup,
    granted_usd,
    spent_usd,
)
from overbae.services.seat_downgrade import enforce_free_seats_after_downgrade

logger = logging.getLogger(__name__)


def _configure_stripe() -> bool:
    key = (getattr(settings, "STRIPE_SECRET_KEY", "") or "").strip()
    if not key:
        return False
    stripe.api_key = key
    # The SDK's default 80s read timeout parks a web worker per request during a
    # Stripe brownout.
    stripe.max_network_retries = 1
    if getattr(stripe.default_http_client, "_overmind_timeout", None) != 20:
        client = stripe.new_default_http_client(timeout=20)
        client._overmind_timeout = 20
        stripe.default_http_client = client
    return True


def _as_dict(obj: Any) -> dict[str, Any]:
    """Stripe SDK objects have no ``.get``; every handler here works on dicts."""
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    to_dict = getattr(obj, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    try:
        return dict(obj)
    except (TypeError, ValueError):
        return {}


def _stripe_id(value: Any) -> str:
    """Normalize a Stripe id that may be a string or expanded object."""
    if not value:
        return ""
    if isinstance(value, str):
        return value
    data = _as_dict(value)
    if data:
        return data.get("id") or ""
    return getattr(value, "id", None) or ""


def _invoice_subscription_id(invoice: dict[str, Any]) -> str:
    """Subscription id from invoice (classic field or newer parent.subscription_details)."""
    sub_id = _stripe_id(invoice.get("subscription"))
    if sub_id:
        return sub_id
    parent = _as_dict(invoice.get("parent"))
    details = _as_dict(parent.get("subscription_details"))
    return _stripe_id(details.get("subscription"))


def _get_or_create_customer(user: User) -> str:
    if user.stripe_customer_id:
        return user.stripe_customer_id

    customer = stripe.Customer.create(
        email=user.email,
        metadata={"user_id": str(user.pk)},
    )
    user.stripe_customer_id = customer.id
    user.save(update_fields=["stripe_customer_id"])
    return customer.id


def _set_user_stripe_customer(user: User, customer_id: str) -> None:
    if not customer_id or user.stripe_customer_id == customer_id:
        return
    user.stripe_customer_id = customer_id
    user.save(update_fields=["stripe_customer_id"])


def _period_timestamp_from_invoice(invoice: dict[str, Any], key: str) -> datetime | None:
    """Stripe reports the invoice line's period in unix seconds."""
    lines = _as_dict(invoice.get("lines")).get("data") or []
    for line in lines:
        period = _as_dict(_as_dict(line).get("period"))
        value = period.get(key)
        if value:
            return datetime.fromtimestamp(int(value), tz=UTC)
    value = invoice.get(f"period_{key}")
    if value:
        return datetime.fromtimestamp(int(value), tz=UTC)
    return None


def _period_end_from_invoice(invoice: dict[str, Any]) -> datetime | None:
    return _period_timestamp_from_invoice(invoice, "end")


def _period_start_from_invoice(invoice: dict[str, Any]) -> datetime | None:
    return _period_timestamp_from_invoice(invoice, "start")


def _cents_to_usd(raw: Any) -> Decimal:
    """Stripe money fields are integer cents; convert to USD."""
    if raw is None:
        return Decimal("0")
    return (Decimal(int(raw)) / Decimal("100")).quantize(Decimal("0.0001"))


def _amount_paid_usd(invoice: dict[str, Any]) -> Decimal:
    return _cents_to_usd(invoice.get("amount_paid"))


def _metadata_user_id(obj: Any) -> str:
    metadata = _as_dict(_as_dict(obj).get("metadata"))
    return str(metadata.get("user_id") or "")


def _resolve_user_for_invoice(invoice: dict[str, Any]) -> User | None:
    customer_id = _stripe_id(invoice.get("customer"))
    subscription_id = _invoice_subscription_id(invoice)

    if subscription_id:
        sub = (
            Subscription.objects.filter(stripe_subscription_id=subscription_id)
            .select_related("user")
            .first()
        )
        if sub:
            return sub.user

    if customer_id:
        user = User.objects.filter(stripe_customer_id=customer_id).first()
        if user:
            return user

    # First invoice may arrive before we stored stripe_subscription_id — read metadata.
    if subscription_id:
        try:
            stripe_sub = stripe.Subscription.retrieve(subscription_id)
            user_id = _metadata_user_id(stripe_sub)
            if user_id:
                return User.objects.filter(pk=user_id).first()
        except stripe.StripeError:
            logger.exception("Failed to retrieve Stripe subscription %s", subscription_id)

    if customer_id:
        try:
            customer = stripe.Customer.retrieve(customer_id)
            user_id = _metadata_user_id(customer)
            if user_id:
                return User.objects.filter(pk=user_id).first()
        except stripe.StripeError:
            logger.exception("Failed to retrieve Stripe customer %s", customer_id)

    return None


@transaction.atomic
def renew_plan_from_invoice(invoice: Any) -> None:
    """Activates or extends Pro, and grants the invoiced amount as credits."""
    invoice = _as_dict(invoice)
    customer_id = _stripe_id(invoice.get("customer"))
    subscription_id = _invoice_subscription_id(invoice)
    invoice_id = _stripe_id(invoice.get("id"))

    # Credit top-ups also produce a paid invoice. Without this guard the
    # customer-id fallback below grants them Pro and double-grants the credits
    # the session handler already granted.
    if not subscription_id:
        logger.info(
            "invoice.paid: no subscription on invoice_id=%s customer=%s — not a plan renewal",
            invoice_id,
            customer_id,
        )
        return

    user = _resolve_user_for_invoice(invoice)

    if user is None:
        logger.error(
            "invoice.paid: could not resolve user (customer=%s subscription=%s)",
            customer_id,
            subscription_id,
        )
        return

    price_id = ""
    lines = _as_dict(invoice.get("lines")).get("data") or []
    if lines:
        price_id = _stripe_id(_as_dict(lines[0]).get("price"))

    sub, _ = Subscription.objects.select_for_update().get_or_create(user=user)

    if invoice_id and sub.last_stripe_invoice_id == invoice_id:
        logger.info(
            "invoice.paid: already processed invoice_id=%s user_id=%s",
            invoice_id,
            user.pk,
        )
        return

    update_fields = ["status", "updated_at"]

    sub.status = SubscriptionStatus.ACTIVE
    _set_user_stripe_customer(user, customer_id)
    if subscription_id and sub.stripe_subscription_id != subscription_id:
        sub.stripe_subscription_id = subscription_id
        update_fields.append("stripe_subscription_id")
    if price_id and sub.stripe_price_id != price_id:
        sub.stripe_price_id = price_id
        update_fields.append("stripe_price_id")

    period_end = _period_end_from_invoice(invoice)
    if period_end is not None:
        sub.end_date = period_end
        update_fields.append("end_date")

    if sub.start_date is None:
        period_start = _period_start_from_invoice(invoice)
        if period_start is not None:
            sub.start_date = period_start
            update_fields.append("start_date")

    grant = _amount_paid_usd(invoice)
    if grant and invoice_id:
        grant_stripe_topup(user, grant, invoice_id)

    if invoice_id:
        sub.last_stripe_invoice_id = invoice_id
        update_fields.append("last_stripe_invoice_id")

    if sub.cancel_at_period_end:
        sub.cancel_at_period_end = False
        update_fields.append("cancel_at_period_end")

    # Thin payload — only what period checks read.
    period_start_unix = None
    period_end_unix = None
    if lines:
        period = _as_dict(_as_dict(lines[0]).get("period"))
        period_start_unix = period.get("start")
        period_end_unix = period.get("end")
    sub.payload = {
        "id": subscription_id,
        "customer": customer_id,
        "status": "active",
        "current_period_start": period_start_unix,
        "current_period_end": period_end_unix,
        "last_invoice_id": invoice_id,
    }
    update_fields.append("payload")

    sub.save(update_fields=update_fields)

    # Pro = unlimited projects
    if user.projects_limit is not None:
        user.projects_limit = None
        user.save(update_fields=["projects_limit"])

    logger.info(
        "Renewed Pro for user_id=%s subscription=%s end_date=%s grant=%s balance=%s",
        user.pk,
        subscription_id,
        period_end,
        grant,
        balance_usd(user),
    )


def _subscription_payload(user: User) -> dict:
    from overbae.services.billing_provider import get_billing
    from overbae.services.plan_limits import is_pro, plan_usage_payload

    billing = get_billing()
    credits = balance_usd(user)
    granted = granted_usd(user)
    usage = plan_usage_payload(user)
    pro = is_pro(user)
    try:
        sub = user.subscription
    except Subscription.DoesNotExist:
        return {
            "billing_enabled": billing.enabled,
            "plan": "pro" if pro else "free",
            "status": None,
            "credits_usd": credits,
            "credits_granted_usd": granted,
            "credits_spent_usd": spent_usd(user),
            "start_date": None,
            "end_date": None,
            "cancel_at_period_end": False,
            "usage": usage,
        }

    return {
        "billing_enabled": billing.enabled,
        "plan": "pro" if pro else "free",
        "status": sub.status,
        "credits_usd": credits,
        "credits_granted_usd": granted,
        "credits_spent_usd": spent_usd(user),
        "start_date": sub.start_date,
        "end_date": sub.end_date,
        "cancel_at_period_end": sub.cancel_at_period_end,
        "usage": usage,
    }


def _stripe_status_to_local(stripe_status: str) -> str | None:
    mapping = {
        "active": SubscriptionStatus.ACTIVE,
        "trialing": SubscriptionStatus.ACTIVE,
        "past_due": SubscriptionStatus.PAST_DUE,
        "canceled": SubscriptionStatus.CANCELED,
        "unpaid": SubscriptionStatus.UNPAID,
        "incomplete": SubscriptionStatus.INCOMPLETE,
        "incomplete_expired": SubscriptionStatus.CANCELED,
        "paused": SubscriptionStatus.CANCELED,
    }
    return mapping.get(stripe_status)


@transaction.atomic
def sync_subscription_from_stripe(stripe_sub: Any) -> None:
    stripe_sub = _as_dict(stripe_sub)
    subscription_id = _stripe_id(stripe_sub.get("id"))
    customer_id = _stripe_id(stripe_sub.get("customer"))
    if not subscription_id:
        return

    sub = (
        Subscription.objects.select_for_update()
        .filter(stripe_subscription_id=subscription_id)
        .select_related("user")
        .first()
    )
    if sub is None and customer_id:
        user = User.objects.filter(stripe_customer_id=customer_id).first()
        if user is not None:
            sub = (
                Subscription.objects.select_for_update()
                .filter(user=user)
                .select_related("user")
                .first()
            )
    if sub is None:
        logger.warning(
            "Stripe subscription sync: no local row (subscription=%s customer=%s)",
            subscription_id,
            customer_id,
        )
        return

    update_fields = ["updated_at"]
    if subscription_id and sub.stripe_subscription_id != subscription_id:
        sub.stripe_subscription_id = subscription_id
        update_fields.append("stripe_subscription_id")
    _set_user_stripe_customer(sub.user, customer_id)

    local_status = _stripe_status_to_local(stripe_sub.get("status") or "")
    if local_status and sub.status != local_status:
        sub.status = local_status
        update_fields.append("status")

    cancel_at_period_end = bool(stripe_sub.get("cancel_at_period_end"))
    if sub.cancel_at_period_end != cancel_at_period_end:
        sub.cancel_at_period_end = cancel_at_period_end
        update_fields.append("cancel_at_period_end")

    period_end = stripe_sub.get("current_period_end")
    if period_end:
        end_date = datetime.fromtimestamp(int(period_end), tz=UTC)
        if sub.end_date != end_date:
            sub.end_date = end_date
            update_fields.append("end_date")

    # Thin, JSON-safe copy — the full Stripe object is not serialisable.
    sub.payload = {
        "id": subscription_id,
        "customer": customer_id,
        "status": stripe_sub.get("status"),
        "cancel_at_period_end": cancel_at_period_end,
        "current_period_end": period_end,
        "current_period_start": stripe_sub.get("current_period_start"),
    }
    update_fields.append("payload")

    sub.save(update_fields=update_fields)

    user = sub.user
    if sub.status == SubscriptionStatus.ACTIVE and user.projects_limit is not None:
        user.projects_limit = None
        user.save(update_fields=["projects_limit"])
    elif sub.status == SubscriptionStatus.CANCELED and user.projects_limit is None:
        user.projects_limit = 5
        user.save(update_fields=["projects_limit"])
        enforce_free_seats_after_downgrade(user)


class SubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Get subscription",
        responses={200: SubscriptionSerializer},
    )
    def get(self, request):
        data = SubscriptionSerializer(_subscription_payload(request.user)).data
        return Response(data)


class BillingLedgerView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = BillingTelemetrySerializer
    pagination_class = PageNumberPagination

    @extend_schema(
        summary="List billing ledger entries",
        responses={200: BillingTelemetrySerializer(many=True)},
    )
    def get(self, request, *args, **kwargs):
        return self.list(request, *args, **kwargs)

    def get_queryset(self):
        return (
            BillingTelemetry.objects.filter(user=self.request.user)
            .select_related("project")
            .order_by("-timestamp")
        )


class CancelSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Cancel subscription at period end",
        request=None,
        responses={200: SubscriptionSerializer},
    )
    def post(self, request):
        if not _configure_stripe():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            sub = request.user.subscription
        except Subscription.DoesNotExist:
            return Response(
                {"detail": "No subscription found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if sub.status != SubscriptionStatus.ACTIVE:
            return Response(
                {"detail": "Only an active subscription can be cancelled."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not sub.stripe_subscription_id:
            return Response(
                {"detail": "Subscription is not linked to Stripe."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if sub.cancel_at_period_end:
            data = SubscriptionSerializer(_subscription_payload(request.user)).data
            return Response(data)

        try:
            stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=True)
        except stripe.StripeError as exc:
            logger.exception("Stripe cancel failed for user_id=%s", request.user.pk)
            message = getattr(exc, "user_message", None) or str(exc) or "Stripe error."
            return Response({"detail": message}, status=status.HTTP_502_BAD_GATEWAY)

        sub.cancel_at_period_end = True
        sub.save(update_fields=["cancel_at_period_end", "updated_at"])
        data = SubscriptionSerializer(_subscription_payload(request.user)).data
        return Response(data)


class RenewSubscriptionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Resume subscription auto-renewal",
        request=None,
        responses={200: SubscriptionSerializer},
    )
    def post(self, request):
        if not _configure_stripe():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            sub = request.user.subscription
        except Subscription.DoesNotExist:
            return Response(
                {"detail": "No subscription found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if sub.status != SubscriptionStatus.ACTIVE:
            return Response(
                {
                    "detail": (
                        "Only an active subscription can be renewed. Buy Pro again to resubscribe."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not sub.stripe_subscription_id:
            return Response(
                {"detail": "Subscription is not linked to Stripe."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not sub.cancel_at_period_end:
            data = SubscriptionSerializer(_subscription_payload(request.user)).data
            return Response(data)

        try:
            stripe.Subscription.modify(sub.stripe_subscription_id, cancel_at_period_end=False)
        except stripe.StripeError as exc:
            logger.exception("Stripe renew failed for user_id=%s", request.user.pk)
            message = getattr(exc, "user_message", None) or str(exc) or "Stripe error."
            return Response({"detail": message}, status=status.HTTP_502_BAD_GATEWAY)

        sub.cancel_at_period_end = False
        sub.save(update_fields=["cancel_at_period_end", "updated_at"])
        data = SubscriptionSerializer(_subscription_payload(request.user)).data
        return Response(data)


class CheckoutSessionView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Create Stripe Checkout Session",
        request=None,
        responses={200: CheckoutSessionResponseSerializer},
    )
    def post(self, request):
        if not _configure_stripe():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        price_id = (getattr(settings, "STRIPE_PRO_PRICE_ID", "") or "").strip()
        if not price_id:
            return Response(
                {"detail": "Stripe price is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        try:
            existing = request.user.subscription
        except Subscription.DoesNotExist:
            existing = None
        if existing and existing.status == SubscriptionStatus.ACTIVE:
            if existing.cancel_at_period_end:
                return Response(
                    {
                        "detail": (
                            "Your plan is set to cancel at period end. Resume auto-renewal instead."
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return Response(
                {"detail": "You already have an active Pro subscription."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        frontend = (getattr(settings, "FRONTEND_URL", "") or "http://localhost:5173").rstrip("/")
        user = request.user

        try:
            customer_id = _get_or_create_customer(user)
            session = stripe.checkout.Session.create(
                mode="subscription",
                customer=customer_id,
                line_items=[{"price": price_id, "quantity": 1}],
                success_url=f"{frontend}/settings?billing=success",
                cancel_url=f"{frontend}/pricing?checkout=cancel",
                client_reference_id=str(user.pk),
                metadata={"user_id": str(user.pk)},
                subscription_data={"metadata": {"user_id": str(user.pk)}},
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe checkout session create failed for user_id=%s", user.pk)
            message = getattr(exc, "user_message", None) or str(exc) or "Stripe error."
            return Response({"detail": message}, status=status.HTTP_502_BAD_GATEWAY)

        if not session.url:
            return Response(
                {"detail": "Stripe did not return a checkout URL."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        data = CheckoutSessionResponseSerializer({"checkout_url": session.url}).data
        return Response(data)


CREDIT_TOPUP_PURPOSE = "credit-topup"


class CreditTopUpView(APIView):
    """Open on every plan, unlike the Pro checkout — running out of credits is
    orthogonal to which plan you are on."""

    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Buy credits (one-time Stripe Checkout)",
        request=CreditTopUpRequestSerializer,
        responses={200: CheckoutSessionResponseSerializer},
    )
    def post(self, request):
        if not _configure_stripe():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        price_id = (getattr(settings, "STRIPE_CREDIT_PRICE_ID", "") or "").strip()
        if not price_id:
            return Response(
                {"detail": "Credit price is not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload = CreditTopUpRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        amount_usd = payload.validated_data["amount_usd"]
        # The Stripe price is one credit at $0.01, so quantity is the credit count.
        credit_quantity = amount_usd * CREDITS_PER_USD

        frontend = (getattr(settings, "FRONTEND_URL", "") or "http://localhost:5173").rstrip("/")
        user = request.user
        metadata = {
            "user_id": str(user.pk),
            "purpose": CREDIT_TOPUP_PURPOSE,
            "amount_usd": str(amount_usd),
            "credits": str(credit_quantity),
        }

        try:
            customer_id = _get_or_create_customer(user)
            session = stripe.checkout.Session.create(
                mode="payment",
                customer=customer_id,
                line_items=[{"price": price_id, "quantity": credit_quantity}],
                success_url=f"{frontend}/settings?billing=topup",
                cancel_url=f"{frontend}/settings?billing=cancel",
                client_reference_id=str(user.pk),
                metadata=metadata,
                payment_intent_data={"metadata": metadata},
            )
        except stripe.StripeError as exc:
            logger.exception("Stripe top-up session create failed for user_id=%s", user.pk)
            message = getattr(exc, "user_message", None) or str(exc) or "Stripe error."
            return Response({"detail": message}, status=status.HTTP_502_BAD_GATEWAY)

        if not session.url:
            return Response(
                {"detail": "Stripe did not return a checkout URL."},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        data = CheckoutSessionResponseSerializer({"checkout_url": session.url}).data
        return Response(data)


def _user_by_pk(raw_id: Any) -> User | None:
    """Tolerant pk lookup: a malformed id must not 500 (Stripe would retry forever)."""
    try:
        return User.objects.filter(pk=raw_id).first()
    except (TypeError, ValueError):
        logger.warning("Ignoring malformed user id from Stripe payload: %r", raw_id)
        return None


def _resolve_user_for_session(session: dict[str, Any]) -> User | None:
    """Precedence: client_reference_id, then metadata, then the customer id."""
    for raw_id in (session.get("client_reference_id"), _metadata_user_id(session)):
        if raw_id:
            user = _user_by_pk(raw_id)
            if user:
                return user

    customer_id = _stripe_id(session.get("customer"))
    if customer_id:
        return User.objects.filter(stripe_customer_id=customer_id).first()
    return None


def grant_credits_from_checkout_session(session: Any) -> None:
    session = _as_dict(session)
    session_id = _stripe_id(session.get("id"))

    if _as_dict(session.get("metadata")).get("purpose") != CREDIT_TOPUP_PURPOSE:
        logger.debug("checkout.session.completed: not a credit top-up (session=%s)", session_id)
        return

    # Async payment methods complete the session before the money lands.
    payment_status = session.get("payment_status")
    if payment_status != "paid":
        logger.info(
            "checkout.session.completed: session=%s not paid yet (payment_status=%s)",
            session_id,
            payment_status,
        )
        return

    user = _resolve_user_for_session(session)
    if user is None:
        logger.error(
            "checkout.session.completed: could not resolve user (session=%s customer=%s)",
            session_id,
            _stripe_id(session.get("customer")),
        )
        return

    # Trust amount_total over request metadata: it is what Stripe actually charged.
    amount = _cents_to_usd(session.get("amount_total"))
    if amount <= 0:
        logger.error("checkout.session.completed: session=%s has no amount_total", session_id)
        return

    _set_user_stripe_customer(user, _stripe_id(session.get("customer")))
    entry = grant_credit_purchase(user, amount, session_id)
    if entry is None:
        logger.info("checkout.session.completed: session=%s already granted", session_id)
        return

    logger.info(
        "Credit top-up granted user_id=%s session=%s amount=%s balance=%s",
        user.pk,
        session_id,
        amount,
        balance_usd(user),
    )


class StripeWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(exclude=True)
    def post(self, request):
        if not _configure_stripe():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        webhook_secret = (getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or "").strip()
        if not webhook_secret:
            logger.error("Stripe webhook received but STRIPE_WEBHOOK_SECRET is not configured.")
            return Response(
                {"detail": "Webhook secret not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        payload = request.body
        sig_header = request.headers.get("Stripe-Signature", "")

        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except ValueError:
            return Response({"detail": "Invalid payload."}, status=status.HTTP_400_BAD_REQUEST)
        except stripe.SignatureVerificationError:
            return Response({"detail": "Invalid signature."}, status=status.HTTP_401_UNAUTHORIZED)

        event_type = event["type"]
        data_object = _as_dict(event["data"]["object"])

        try:
            if event_type == "invoice.paid":
                renew_plan_from_invoice(data_object)
            elif event_type in (
                "checkout.session.completed",
                # Delayed methods leave the completed session unpaid, so this is
                # where the money actually arrives. Granting is keyed on the
                # session id, so handling both can only ever credit once.
                "checkout.session.async_payment_succeeded",
            ):
                grant_credits_from_checkout_session(data_object)
            elif event_type in (
                "customer.subscription.updated",
                "customer.subscription.deleted",
            ):
                sync_subscription_from_stripe(data_object)
            else:
                logger.debug("Stripe webhook ignored event: %s", event_type)
        except Exception:
            logger.exception("%s handler failed", event_type)
            return Response(
                {"detail": "Webhook handling error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        return Response(status=status.HTTP_204_NO_CONTENT)
