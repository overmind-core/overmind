"""Cross-tenant product usage metrics for Overwatch. Guest users and the
projects only guests belong to are excluded everywhere."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from statistics import median
from time import monotonic
from typing import Any

from django.db import connection, transaction
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
from django.utils import timezone

from overbae.models import (
    BillingService,
    BillingTelemetry,
    Capability,
    Dataset,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    InferenceCall,
    OptimizerExperiment,
    Project,
    ProjectMembership,
    Span,
    Subscription,
    SubscriptionStatus,
    User,
)

ACTIVATION_STEPS = (
    "signed_up",
    "joined_project",
    "sent_first_span",
    "synced_capabilities",
    "ran_evaluation",
    "started_training",
    "deployed_model",
    "called_alias",
)
RESETTABLE_MILESTONE_STEPS = frozenset({"deployed_model", "called_alias"})
TOP_BASE_MODELS = 5
RECENT_TRAINING_JOBS = 20
LOW_BALANCE_USD = Decimal("5")
USD = DecimalField(max_digits=12, decimal_places=7)

TRAINING_OUTCOMES = {
    FinetuningJob.Status.SUCCEEDED: "completed",
    FinetuningJob.Status.FAILED: "failed",
    FinetuningJob.Status.CANCELLED: "cancelled",
}


@contextmanager
def analytics_read_budget(seconds: float = 15):
    """Bound each SQL statement and stop a multi-query report after its budget."""
    deadline = monotonic() + seconds

    def bounded(execute, sql, params, many, context):
        if monotonic() >= deadline:
            raise TimeoutError("analytics_read_timeout")
        return execute(sql, params, many, context)

    with transaction.atomic():
        previous = None
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SHOW statement_timeout")
                previous = cursor.fetchone()[0]
                cursor.execute(
                    "SELECT set_config('statement_timeout', %s, true)",
                    [f"{max(1, min(5000, int(seconds * 1000)))}ms"],
                )
        with connection.execute_wrapper(bounded):
            yield
        # On failure the atomic rollback restores SET LOCAL automatically.
        if previous is not None:
            with connection.cursor() as cursor:
                cursor.execute("SELECT set_config('statement_timeout', %s, true)", [previous])


def window_start(days: int) -> datetime:
    """Start of the first UTC calendar day, including today in the day count."""
    if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 365:
        raise ValueError("days must be an integer from 1 to 365")
    return timezone.now().astimezone(UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=days - 1)


def _users() -> QuerySet[User]:
    return User.objects.filter(is_guest=False)


def _user_project_ids() -> QuerySet[ProjectMembership]:
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


def _daily_by(
    queryset: QuerySet, field: str, group: str, bucket: Callable[[str], str]
) -> list[dict[str, Any]]:
    days: dict[datetime, dict[str, int]] = {}
    for row in (
        queryset.annotate(day=TruncDay(field))
        .values("day", group)
        .annotate(n=Count("pk"))
        .order_by("day")
    ):
        values = days.setdefault(row["day"], {})
        key = bucket(row[group])
        values[key] = values.get(key, 0) + row["n"]
    return [{"day": day, "values": values} for day, values in days.items()]


def _training_outcome(status: str) -> str:
    return TRAINING_OUTCOMES.get(status, "in_progress")


def _workflow(queryset: QuerySet, since: datetime) -> dict[str, Any]:
    return {
        "total": queryset.count(),
        "by_status": _counts(queryset, "status"),
        "daily": _daily(queryset.filter(created_at__gte=since), "created_at"),
    }


def _scalar(queryset: QuerySet, aggregate: Aggregate) -> Subquery:
    return Subquery(
        queryset.order_by().annotate(grp=Value(1)).values("grp").annotate(v=aggregate).values("v")
    )


def _reached() -> dict[str, Exists]:
    """Per-user activation flags, keyed ``reached_<step>``; correlate on the outer user."""
    member = OuterRef("pk")
    return {
        "reached_joined_project": Exists(ProjectMembership.objects.filter(user=member)),
        "reached_sent_first_span": Exists(
            Span.objects.filter(
                project__memberships__user=member,
                received_at__gte=F("project__memberships__created_at"),
            )
        ),
        "reached_synced_capabilities": Exists(
            Capability.objects.filter(
                project__memberships__user=member,
                observed=False,
                created_at__gte=F("project__memberships__created_at"),
            ).exclude(status=Capability.Status.DELETED)
        ),
        "reached_ran_evaluation": Exists(
            EvalRun.objects.filter(
                project__memberships__user=member,
                created_at__gte=F("project__memberships__created_at"),
            )
        ),
        "reached_started_training": Exists(
            FinetuningJob.objects.filter(
                project__memberships__user=member,
                created_at__gte=F("project__memberships__created_at"),
            )
        ),
        "reached_deployed_model": Exists(
            DeployedModel.objects.filter(
                project__memberships__user=member,
                deployed_at__isnull=False,
                deployed_at__gte=F("project__memberships__created_at"),
            )
        ),
        "reached_called_alias": Exists(
            Capability.objects.filter(
                project__memberships__user=member,
                first_application_request_at__isnull=False,
                first_application_request_at__gte=F("project__memberships__created_at"),
            )
        ),
    }


def stage(user: User) -> str:
    """The furthest activation step an annotated user row reached."""
    reached = [step for step in ACTIVATION_STEPS[1:] if getattr(user, f"reached_{step}")]
    return reached[-1] if reached else ACTIVATION_STEPS[0]


def _active_users(since: datetime) -> int:
    return (
        _users()
        .filter(
            Exists(
                Span.objects.filter(
                    Q(received_at__gte=since),
                    project__memberships__user=OuterRef("pk"),
                    received_at__gte=F("project__memberships__created_at"),
                )
            )
        )
        .count()
    )


def overview(since: datetime, active_since: datetime) -> dict[str, Any]:
    users = _users()
    project_ids = _user_project_ids()
    spans = Span.objects.filter(project_id__in=project_ids, received_at__gte=since)
    jobs = FinetuningJob.objects.filter(project_id__in=project_ids, created_at__gte=since)
    subscriptions = Subscription.objects.filter(user__is_guest=False)
    ledger = BillingTelemetry.objects.filter(user__is_guest=False)
    window_ledger = ledger.filter(timestamp__gte=since)
    subscription_counts = _counts(subscriptions, "status")
    ledger_balances = (
        ledger.order_by()
        .values("user")
        .annotate(
            balance=Sum("amount"),
            spent=Sum("amount", filter=Q(amount__lt=0)),
        )
    )
    return {
        "users_total": users.count(),
        "signups": _daily(users.filter(date_joined__gte=since), "date_joined"),
        "sign_on_methods": _counts(users, "sign_on_method"),
        "projects_created": _daily(
            Project.objects.filter(pk__in=project_ids, created_at__gte=since), "created_at"
        ),
        "trace_active_users_7d": _active_users(active_since),
        "trace_active_users_window": _active_users(since),
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
        "subscriptions": subscription_counts,
        "paying_users": subscription_counts.get(SubscriptionStatus.ACTIVE, 0),
        "cancelling_users": subscriptions.filter(
            status=SubscriptionStatus.ACTIVE, cancel_at_period_end=True
        ).count(),
        "free_users": users.filter(project_memberships__isnull=False)
        .exclude(pk__in=subscriptions.filter(status=SubscriptionStatus.ACTIVE).values("user_id"))
        .distinct()
        .count(),
        "low_balance_users": ledger_balances.filter(
            balance__lt=LOW_BALANCE_USD, spent__isnull=False
        ).count(),
        "topups_usd": _daily(
            window_ledger.filter(service=BillingService.STRIPE_TOPUP, amount__gt=0),
            "timestamp",
            Sum("amount"),
        ),
        "spend_usd": _daily(
            window_ledger.filter(amount__lt=0), "timestamp", Sum(-F("amount"), output_field=USD)
        ),
        "spend_by_service": {
            service: -total
            for service, total in window_ledger.filter(amount__lt=0)
            .order_by()
            .values_list("service")
            .annotate(total=Sum("amount"))
        },
    }


def activation(since: datetime) -> dict[str, Any]:
    cohort = _users().filter(date_joined__gte=since)
    counts = cohort.annotate(**_reached()).aggregate(
        signed_up=Count("pk"),
        **{
            step: Count("pk", filter=Q(**{f"reached_{step}": True}))
            for step in ACTIVATION_STEPS[1:]
        },
    )
    first_span = (
        Span.objects.filter(
            project__memberships__user=OuterRef("pk"),
            received_at__gte=F("project__memberships__created_at"),
        )
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
        "steps": [{"step": step, "users": counts[step]} for step in ACTIVATION_STEPS],
        "median_hours_to_first_span": median(hours) if hours else None,
    }


def milestone_customers(since: datetime, *, step: str, limit: int) -> dict[str, Any]:
    """Count retained milestones separately from the bounded newest-customer list."""
    if step in RESETTABLE_MILESTONE_STEPS:
        raise ValueError("First-time deployment and alias activity cannot be determined.")
    if not 1 <= limit <= 25:
        raise ValueError("Invalid customer report limit.")
    until = timezone.now()
    if step == "signed_up":
        matched = _users().filter(date_joined__gte=since, date_joined__lte=until)
        rows = matched.annotate(at=F("date_joined"))
    else:
        sources = {
            "joined_project": (ProjectMembership, "user_id", "created_at", Q(user__is_guest=False)),
            "sent_first_span": (Span, "project__memberships__user_id", "received_at", Q()),
            "synced_capabilities": (
                Capability,
                "project__memberships__user_id",
                "created_at",
                Q(observed=False) & ~Q(status=Capability.Status.DELETED),
            ),
            "ran_evaluation": (EvalRun, "project__memberships__user_id", "created_at", Q()),
            "started_training": (FinetuningJob, "project__memberships__user_id", "created_at", Q()),
        }
        model, user_field, at_field, condition = sources[step]
        if step != "joined_project":
            condition &= Q(
                project__memberships__user__is_guest=False,
                **{f"{at_field}__gte": F("project__memberships__created_at")},
            )
        # Correlate on a scalar user PK across all memberships. Earlier retained
        # activity excludes the user even if they reached the step in another project.
        earlier = model.objects.filter(
            condition, **{user_field: OuterRef("pk"), f"{at_field}__lt": since}
        )
        candidates = model.objects.filter(
            condition,
            **{user_field: OuterRef("pk"), f"{at_field}__gte": since, f"{at_field}__lte": until},
        )
        matched = _users().filter(Exists(candidates), ~Exists(earlier))
        rows = matched.annotate(at=Subquery(candidates.order_by(at_field).values(at_field)[:1]))
    return {
        "total": matched.count(),
        "customers": [
            {"email": row["email"], "reached_at": row["at"].isoformat()}
            for row in rows.order_by("-at", "pk").values("email", "at")[:limit]
        ],
    }


def users(since: datetime) -> QuerySet[User]:
    member = OuterRef("pk")
    spans = Span.objects.filter(project__memberships__user=member)
    ledger = BillingTelemetry.objects.filter(user=member)
    zero_usd = Value(Decimal("0"), output_field=USD)
    return _users().annotate(
        **_reached(),
        projects=Coalesce(_scalar(ProjectMembership.objects.filter(user=member), Count("pk")), 0),
        seats=Coalesce(
            _scalar(
                ProjectMembership.objects.filter(
                    project__memberships__user=member, user__is_guest=False
                ),
                Count("user", distinct=True),
            ),
            0,
        ),
        last_span_at=_scalar(spans, Max("received_at")),
        spans=Coalesce(_scalar(spans.filter(received_at__gte=since), Count("pk")), 0),
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
        subscription_status=F("subscription__status"),
        spend_usd=Coalesce(
            _scalar(
                ledger.filter(amount__lt=0, timestamp__gte=since),
                Sum(-F("amount"), output_field=USD),
            ),
            zero_usd,
        ),
        topups_usd=Coalesce(
            _scalar(
                ledger.filter(service=BillingService.STRIPE_TOPUP, amount__gt=0),
                Sum("amount"),
            ),
            zero_usd,
        ),
        balance_usd=Coalesce(_scalar(ledger, Sum("amount")), zero_usd),
    )


def user_activity(user: User, since: datetime) -> dict[str, Any]:
    """Lifetime totals and in-window daily series across the user's projects."""
    project_ids = ProjectMembership.objects.filter(user=user).values("project_id")
    jobs = FinetuningJob.objects.filter(project_id__in=project_ids)
    totals = {"started": 0, "completed": 0, "failed": 0, "cancelled": 0, "in_progress": 0}
    for status, n in _counts(jobs, "status").items():
        totals["started"] += n
        totals[_training_outcome(status)] += n
    roots = Span.objects.filter(project_id__in=project_ids, parent_span_id__isnull=True)
    calls = InferenceCall.objects.filter(project_id__in=project_ids)
    by_model = _counts(calls, "deployed_model__model_id")
    datasets = Dataset.objects.filter(project_id__in=project_ids)
    ledger = BillingTelemetry.objects.filter(user=user)
    spend = Sum(-F("amount"), output_field=USD)
    topups = Sum("amount")
    return {
        "user": user,
        "billing": {
            "subscription_status": getattr(user, "subscription_status", None),
            "spend_usd": ledger.filter(amount__lt=0).aggregate(value=spend)["value"]
            or Decimal("0"),
            "topups_usd": ledger.filter(
                service=BillingService.STRIPE_TOPUP, amount__gt=0
            ).aggregate(value=topups)["value"]
            or Decimal("0"),
            "balance_usd": ledger.aggregate(value=Sum("amount"))["value"] or Decimal("0"),
            "spend_daily": _daily(
                ledger.filter(amount__lt=0, timestamp__gte=since), "timestamp", spend
            ),
            "topups_daily": _daily(
                ledger.filter(
                    service=BillingService.STRIPE_TOPUP,
                    amount__gt=0,
                    timestamp__gte=since,
                ),
                "timestamp",
                topups,
            ),
        },
        "training": {
            "totals": totals,
            "daily": _daily_by(
                jobs.filter(created_at__gte=since), "created_at", "status", _training_outcome
            ),
            "recent": list(
                jobs.order_by("-created_at").values(
                    "id", "base_model", "status", "created_at", "completed_at"
                )[:RECENT_TRAINING_JOBS]
            ),
        },
        "traces": {
            "total": roots.count(),
            "daily": _daily(roots.filter(received_at__gte=since), "received_at"),
        },
        "inference": {
            "calls": sum(by_model.values()),
            "models": len(by_model),
            "by_model": by_model,
            "daily": _daily(calls.filter(created_at__gte=since), "created_at"),
        },
        "datasets": {
            "total": datasets.count(),
            "by_source": _counts(datasets, "source_kind"),
            "by_intent": _counts(datasets, "intent"),
            "daily": _daily(datasets.filter(created_at__gte=since), "created_at"),
        },
        "optimizer": _workflow(
            OptimizerExperiment.objects.filter(project_id__in=project_ids), since
        ),
        "evaluations": _workflow(EvalRun.objects.filter(project_id__in=project_ids), since),
    }
