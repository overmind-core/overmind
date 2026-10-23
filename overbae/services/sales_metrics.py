"""Cross-tenant metrics for the internal sales dashboard. Guest users and the
projects only guests belong to are excluded everywhere."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from statistics import median
from typing import Any

from django.db.models import (
    Aggregate,
    Count,
    DecimalField,
    Exists,
    F,
    Max,
    OuterRef,
    Q,
    QuerySet,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, TruncDay

from overbae.models import (
    BillingService,
    BillingTelemetry,
    Capability,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    InferenceCall,
    Project,
    ProjectMembership,
    Span,
    Subscription,
    SubscriptionStatus,
    User,
)

FUNNEL_STEPS = (
    "signed_up",
    "created_project",
    "sent_first_span",
    "synced_capabilities",
    "ran_evaluation",
    "started_training",
    "deployed_model",
    "called_alias",
)
LOW_BALANCE_USD = Decimal("5")
TOP_BASE_MODELS = 5

USD = DecimalField(max_digits=12, decimal_places=7)


def _customers() -> QuerySet[User]:
    return User.objects.filter(is_guest=False)


def _customer_project_ids() -> QuerySet[ProjectMembership]:
    return ProjectMembership.objects.filter(user__is_guest=False).values("project_id")


def _daily(queryset: QuerySet, field: str, value: Aggregate | None = None) -> list[dict[str, Any]]:
    return list(
        queryset.annotate(day=TruncDay(field))
        .values("day")
        .annotate(value=value or Count("pk"))
        .order_by("day")
        .values("day", "value")
    )


def _counts(queryset: QuerySet, field: str) -> dict[str, int]:
    return dict(queryset.order_by().values_list(field).annotate(n=Count("pk")))


def _scalar(queryset: QuerySet, aggregate: Aggregate) -> Subquery:
    return Subquery(
        queryset.order_by().annotate(grp=Value(1)).values("grp").annotate(v=aggregate).values("v")
    )


def _reached() -> dict[str, Exists]:
    """Per-user funnel flags, keyed ``reached_<step>``; correlate on the outer user."""
    member = OuterRef("pk")
    return {
        "reached_created_project": Exists(ProjectMembership.objects.filter(user=member)),
        "reached_sent_first_span": Exists(Span.objects.filter(project__memberships__user=member)),
        "reached_synced_capabilities": Exists(
            Capability.objects.filter(project__memberships__user=member, observed=False).exclude(
                status=Capability.Status.DELETED
            )
        ),
        "reached_ran_evaluation": Exists(EvalRun.objects.filter(project__memberships__user=member)),
        "reached_started_training": Exists(
            FinetuningJob.objects.filter(project__memberships__user=member)
        ),
        "reached_deployed_model": Exists(
            DeployedModel.objects.filter(
                project__memberships__user=member, deployed_at__isnull=False
            )
        ),
        "reached_called_alias": Exists(
            Capability.objects.filter(
                project__memberships__user=member, first_application_request_at__isnull=False
            )
        ),
    }


def stage(account: User) -> str:
    """The furthest funnel step an annotated ``accounts()`` row reached."""
    reached = [step for step in FUNNEL_STEPS[1:] if getattr(account, f"reached_{step}")]
    return reached[-1] if reached else FUNNEL_STEPS[0]


def _active_accounts(since: datetime) -> int:
    return (
        _customers()
        .filter(
            Exists(
                Span.objects.filter(
                    project__memberships__user=OuterRef("pk"), received_at__gte=since
                )
            )
        )
        .count()
    )


def overview(since: datetime, active_since: datetime) -> dict[str, Any]:
    customers = _customers()
    project_ids = _customer_project_ids()
    subscriptions = Subscription.objects.filter(user__is_guest=False)
    ledger = BillingTelemetry.objects.filter(user__is_guest=False)
    window_ledger = ledger.filter(timestamp__gte=since)
    spans = Span.objects.filter(project_id__in=project_ids, received_at__gte=since)
    jobs = FinetuningJob.objects.filter(project_id__in=project_ids, created_at__gte=since)
    status_counts = _counts(subscriptions, "status")
    return {
        "accounts_total": customers.count(),
        "signups": _daily(customers.filter(date_joined__gte=since), "date_joined"),
        "sign_on_methods": _counts(customers, "sign_on_method"),
        "projects_created": _daily(
            Project.objects.filter(pk__in=project_ids, created_at__gte=since), "created_at"
        ),
        "active_accounts_7d": _active_accounts(active_since),
        "active_accounts_window": _active_accounts(since),
        "subscriptions": status_counts,
        "paying_accounts": status_counts.get(SubscriptionStatus.ACTIVE, 0),
        "cancelling_accounts": subscriptions.filter(
            status=SubscriptionStatus.ACTIVE, cancel_at_period_end=True
        ).count(),
        "free_accounts": customers.filter(project_memberships__isnull=False)
        .exclude(pk__in=subscriptions.filter(status=SubscriptionStatus.ACTIVE).values("user_id"))
        .distinct()
        .count(),
        "low_balance_accounts": ledger.order_by()
        .values("user")
        .annotate(balance=Sum("amount"), spent=Sum("amount", filter=Q(amount__lt=0)))
        .filter(balance__lt=LOW_BALANCE_USD, spent__isnull=False)
        .count(),
        "topups_usd": _daily(
            window_ledger.filter(service=BillingService.STRIPE_TOPUP, amount__gt=0),
            "timestamp",
            Sum("amount"),
        ),
        "spend_by_service": {
            service: -total
            for service, total in window_ledger.filter(amount__lt=0)
            .order_by()
            .values_list("service")
            .annotate(total=Sum("amount"))
        },
        "spans": _daily(spans, "received_at"),
        "traces": _daily(spans.filter(parent_span_id__isnull=True), "received_at"),
        "training_jobs": _counts(jobs, "status"),
        "top_base_models": list(
            jobs.order_by()
            .values("base_model")
            .annotate(jobs=Count("pk"))
            .order_by("-jobs", "base_model")[:TOP_BASE_MODELS]
        ),
        "live_deployments": DeployedModel.objects.filter(
            project_id__in=project_ids, status=DeployedModel.Status.READY
        ).count(),
        "inference_calls": _daily(
            InferenceCall.objects.filter(project_id__in=project_ids, created_at__gte=since),
            "created_at",
        ),
    }


def funnel(since: datetime) -> dict[str, Any]:
    cohort = _customers().filter(date_joined__gte=since)
    counts = cohort.annotate(**_reached()).aggregate(
        signed_up=Count("pk"),
        **{step: Count("pk", filter=Q(**{f"reached_{step}": True})) for step in FUNNEL_STEPS[1:]},
    )
    first_span = (
        Span.objects.filter(project__memberships__user=OuterRef("pk"))
        .order_by("received_at")
        .values("received_at")[:1]
    )
    hours = [
        max((first - joined).total_seconds(), 0) / 3600
        for joined, first in cohort.annotate(first=Subquery(first_span))
        .filter(first__isnull=False)
        .values_list("date_joined", "first")
    ]
    return {
        "steps": [{"step": step, "accounts": counts[step]} for step in FUNNEL_STEPS],
        "median_hours_to_first_span": median(hours) if hours else None,
    }


def accounts(since: datetime) -> QuerySet[User]:
    member = OuterRef("pk")
    spans = Span.objects.filter(project__memberships__user=member)
    ledger = BillingTelemetry.objects.filter(user=member)
    zero_usd = Value(Decimal("0"), output_field=USD)
    return _customers().annotate(
        **_reached(),
        subscription_status=F("subscription__status"),
        projects=Coalesce(_scalar(ProjectMembership.objects.filter(user=member), Count("pk")), 0),
        seats=Coalesce(
            _scalar(
                ProjectMembership.objects.filter(project__memberships__user=member),
                Count("user", distinct=True),
            ),
            0,
        ),
        last_span_at=_scalar(spans, Max("received_at")),
        spans=Coalesce(_scalar(spans.filter(received_at__gte=since), Count("pk")), 0),
        spend_usd=Coalesce(
            _scalar(
                ledger.filter(amount__lt=0, timestamp__gte=since),
                Sum(-F("amount"), output_field=USD),
            ),
            zero_usd,
        ),
        topups_usd=Coalesce(
            _scalar(
                ledger.filter(service=BillingService.STRIPE_TOPUP, amount__gt=0), Sum("amount")
            ),
            zero_usd,
        ),
        balance_usd=Coalesce(_scalar(ledger, Sum("amount")), zero_usd),
        training_jobs=Coalesce(
            _scalar(FinetuningJob.objects.filter(project__memberships__user=member), Count("pk")),
            0,
        ),
        live_deployments=Coalesce(
            _scalar(
                DeployedModel.objects.filter(
                    project__memberships__user=member, status=DeployedModel.Status.READY
                ),
                Count("pk"),
            ),
            0,
        ),
    )
