from __future__ import annotations

import logging

import stripe
from django.conf import settings
from django.db.models import Case, F, Value, When
from django.db.models.functions import Concat, Replace, Substr, Upper
from drf_spectacular.utils import extend_schema
from rest_framework import generics, status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from overbae.api.config import PageNumberPagination
from overbae.api.ordering import TableOrderingFilter
from overbae.api.serializers import (
    BillingAccountSerializer,
    BillingTelemetrySerializer,
    CheckoutSessionResponseSerializer,
    CreditTopUpConfirmationSerializer,
    CreditTopUpConfirmRequestSerializer,
    CreditTopUpRequestSerializer,
)
from overbae.models import BillingService, BillingTelemetry
from overbae.services.billing_ledger import account_payload
from overbae.services.credit_purchases import (
    WEBHOOK_EVENTS,
    PurchaseUnavailable,
    SessionNotFound,
    configure_stripe,
    confirm_session,
    create_topup_session,
    grant_from_session,
)

logger = logging.getLogger(__name__)


def _stripe_failure() -> Response:
    # Stripe's error text names request ids and account settings; it stays in the log.
    return Response(
        {"detail": "Stripe did not answer. Try again."}, status=status.HTTP_502_BAD_GATEWAY
    )


class BillingAccountView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(summary="Get the credits account", responses={200: BillingAccountSerializer})
    def get(self, request):
        return Response(BillingAccountSerializer(account_payload(request.user)).data)


class BillingLedgerView(generics.ListAPIView):
    permission_classes = [IsAuthenticated]
    serializer_class = BillingTelemetrySerializer
    filter_backends = [TableOrderingFilter]
    ordering_aliases = {"service": "service_name"}
    ordering_fields = ["timestamp", "service", "project__name", "amount"]
    ordering = ["-timestamp", "id"]
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
            .annotate(
                service_name=Case(
                    *[
                        When(service=key, then=Value(label))
                        for key, label in BillingService.choices
                    ],
                    default=Concat(
                        Upper(Substr("service", 1, 1)),
                        Substr(Replace(F("service"), Value("-"), Value(" ")), 2),
                    ),
                )
            )
            .order_by("-timestamp")
        )


class CreditTopUpView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Buy credits (one-time Stripe Checkout)",
        request=CreditTopUpRequestSerializer,
        responses={200: CheckoutSessionResponseSerializer},
    )
    def post(self, request):
        if not configure_stripe():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        payload = CreditTopUpRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            url = create_topup_session(request.user, payload.validated_data["amount_usd"])
        except PurchaseUnavailable:
            return Response(
                {"detail": "Credit purchases are not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except stripe.StripeError:
            logger.exception("Stripe top-up session create failed for user_id=%s", request.user.pk)
            return _stripe_failure()
        return Response(CheckoutSessionResponseSerializer({"checkout_url": url}).data)


class CreditTopUpConfirmView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        summary="Confirm a credit purchase after Stripe Checkout",
        request=CreditTopUpConfirmRequestSerializer,
        responses={200: CreditTopUpConfirmationSerializer},
    )
    def post(self, request):
        if not configure_stripe():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        payload = CreditTopUpConfirmRequestSerializer(data=request.data)
        payload.is_valid(raise_exception=True)
        try:
            paid = confirm_session(request.user, payload.validated_data["session_id"])
        except SessionNotFound:
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
        except stripe.StripeError:
            logger.exception("Stripe session confirm failed for user_id=%s", request.user.pk)
            return _stripe_failure()
        data = {"paid": paid, "account": account_payload(request.user)}
        return Response(CreditTopUpConfirmationSerializer(data).data)


class StripeWebhookView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(exclude=True)
    def post(self, request):
        if not configure_stripe():
            return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

        webhook_secret = (getattr(settings, "STRIPE_WEBHOOK_SECRET", "") or "").strip()
        if not webhook_secret:
            logger.error("Stripe webhook received but STRIPE_WEBHOOK_SECRET is not configured.")
            return Response(
                {"detail": "Webhook secret not configured."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

        sig_header = request.headers.get("Stripe-Signature", "")
        try:
            event = stripe.Webhook.construct_event(request.body, sig_header, webhook_secret)
        except ValueError:
            return Response({"detail": "Invalid payload."}, status=status.HTTP_400_BAD_REQUEST)
        except stripe.SignatureVerificationError:
            return Response({"detail": "Invalid signature."}, status=status.HTTP_401_UNAUTHORIZED)

        if event["type"] not in WEBHOOK_EVENTS:
            return Response(status=status.HTTP_204_NO_CONTENT)
        try:
            grant_from_session(event["data"]["object"])
        except Exception:
            logger.exception("%s handler failed", event["type"])
            return Response(
                {"detail": "Webhook handling error."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)
