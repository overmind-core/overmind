"""Private customer lookup and usage reports over retained product records."""

from datetime import UTC, timedelta

from django.db.models import Case, CharField, Count, F, Max, Q, Sum, Value, When
from django.db.models.functions import TruncDate
from django.utils import timezone

from overbae.models import (
    BillingService,
    BillingTelemetry,
    DeployedModel,
    EvalRun,
    FinetuningJob,
    InferenceCall,
    ProjectMembership,
    Span,
    Subscription,
    User,
)
from overbae.services.user_insights import window_start


def _label(value, limit=100):
    return " ".join(str(value).split())[:limit]


def _matches(arguments):
    users = User.objects.filter(is_guest=False)
    search = arguments.get("email", arguments.get("query", "")).strip()
    if "@" in search:
        users = users.filter(email__iexact=search)
    else:
        for word in search.split():
            users = users.filter(
                Q(first_name__icontains=word)
                | Q(last_name__icontains=word)
                | Q(email__icontains=word)
            )
    return list(
        users.only("id", "email", "first_name", "last_name", "date_joined").order_by("email")[:6]
    )


def _totals(calls):
    return calls.aggregate(
        calls=Count("pk"),
        succeeded=Count("pk", filter=Q(outcome="succeeded")),
        input_tokens=Sum("prompt_tokens", default=0),
        output_tokens=Sum("completion_tokens", default=0),
        active_days=Count(
            TruncDate("created_at", tzinfo=UTC), distinct=True, filter=Q(outcome="succeeded")
        ),
        projects=Count("project_id", distinct=True, filter=Q(outcome="succeeded")),
    )


def _usage(project_ids, days, now):
    since = window_start(days)
    calls = InferenceCall.objects.filter(project_id__in=project_ids, created_at__lte=now)
    current = calls.filter(created_at__gte=since)
    totals = _totals(current)
    previous = _totals(
        calls.filter(created_at__gte=since - timedelta(days=days), created_at__lt=since)
    )
    sources = list(
        current.order_by()
        .annotate(
            origin=Case(
                When(source__in=["application", "playground", "internal"], then=F("source")),
                default=Value("unknown"),
                output_field=CharField(),
            )
        )
        .values("origin")
        .annotate(
            calls=Count("pk"),
            succeeded=Count("pk", filter=Q(outcome="succeeded")),
            input_tokens=Sum("prompt_tokens"),
            output_tokens=Sum("completion_tokens"),
        )
        .order_by("origin")
    )
    models = list(
        current.order_by()
        .values("deployed_model__model_id")
        .annotate(
            calls=Count("pk"),
            succeeded=Count("pk", filter=Q(outcome="succeeded")),
            input_tokens=Sum("prompt_tokens"),
            output_tokens=Sum("completion_tokens"),
        )
        .order_by("-calls", "deployed_model__model_id")[:5]
    )
    last = calls.filter(outcome="succeeded").aggregate(at=Max("created_at"))["at"]
    return totals, previous, sources, models, last


def model_usage(arguments, user=None):
    days = arguments["days"]
    now = timezone.now()
    since = window_start(days)
    memberships = (
        ProjectMembership.objects.filter(user=user)
        if user
        else ProjectMembership.objects.filter(user__is_guest=False)
    )
    project_ids = memberships.values("project_id")
    totals, previous, sources, models, last = _usage(project_ids, days, now)
    lines = [
        f"Model usage in the last {days} UTC days (today is partial):",
        f"{totals['succeeded']:,} successful / {totals['calls'] - totals['succeeded']:,} unsuccessful recorded calls; {totals['active_days']} active days; {totals['projects']} active projects.",
        f"Recorded tokens: {totals['input_tokens']:,} input / {totals['output_tokens']:,} output (all outcomes).",
        f"Previous {days} UTC days: {previous['succeeded']:,} successful calls; {previous['input_tokens'] + previous['output_tokens']:,} recorded tokens. Previous period contains full days.",
        f"Last successful model call: {last.isoformat() if last else 'none recorded'}.",
    ]
    source_labels = {
        "application": "Application",
        "playground": "Playground",
        "internal": "Internal",
        "unknown": "Unknown source",
    }
    for row in sources:
        lines.append(
            f"{source_labels[row['origin']]}: {row['succeeded']:,} successful / {row['calls'] - row['succeeded']:,} unsuccessful; {row['input_tokens'] + row['output_tokens']:,} tokens."
        )
    if user:
        traces = Span.objects.filter(
            project_id__in=project_ids,
            parent_span_id__isnull=True,
            received_at__gte=since,
            received_at__lte=now,
        ).count()
        jobs = FinetuningJob.objects.filter(
            project_id__in=project_ids, created_at__gte=since, created_at__lte=now
        ).count()
        evaluations = EvalRun.objects.filter(
            project_id__in=project_ids, created_at__gte=since, created_at__lte=now
        ).count()
        ready = DeployedModel.objects.filter(
            project_id__in=project_ids, status=DeployedModel.Status.READY
        ).count()
        ledger = BillingTelemetry.objects.filter(user=user, timestamp__lte=now).aggregate(
            balance=Sum("amount", default=0),
            lifetime_spend=Sum(-F("amount"), filter=Q(amount__lt=0), default=0),
            lifetime_topups=Sum(
                "amount", filter=Q(amount__gt=0, service=BillingService.STRIPE_TOPUP), default=0
            ),
            spend=Sum(-F("amount"), filter=Q(amount__lt=0, timestamp__gte=since), default=0),
        )
        subscription = (
            Subscription.objects.filter(user=user).values_list("status", flat=True).first()
        )
        lines.extend(
            [
                f"In this window: {traces:,} root traces, {jobs} training jobs started, {evaluations} evaluations started. Ready deployments now: {ready}.",
                f"User billing: ${ledger['spend']:.2f} spent in window; ${ledger['balance']:.2f} current balance.",
                f"${ledger['lifetime_spend']:.2f} lifetime spend; ${ledger['lifetime_topups']:.2f} lifetime top-ups. Subscription: {subscription or 'none'}.",
            ]
        )
    lines.extend(
        [
            "Calls belong to shared projects, not an identified individual caller. Application calls indicate API usage; they do not prove production use.",
            "Tokens cover retained hosted-model calls only, not external models or training. Historical zero token fields may mean unavailable telemetry.",
        ]
    )
    if models:
        lines.append("Top models by recorded calls (up to 5):")
        for row in models:
            line = f"{_label(row['deployed_model__model_id'], 80)}: {row['succeeded']:,} successful / {row['calls'] - row['succeeded']:,} unsuccessful; {row['input_tokens']:,} input / {row['output_tokens']:,} output."
            if len("\n".join(lines)) + len(line) > 2300:
                lines.append("Additional model rows omitted to fit Slack.")
                break
            lines.append(line)
    return "\n".join(lines)


def customer_activity(arguments):
    matches = _matches(arguments)
    if not matches:
        return "No matching customer was found. Try their full name or exact email."
    if len(matches) > 1:
        choices = "\n".join(
            f"{_label(user.get_full_name(), 60)}: {_label(user.email, 254)}" for user in matches[:5]
        )
        return f"Multiple customers match. Reply with the exact email. Showing up to 5 matches:\n{choices}"
    user = matches[0]
    return f"Customer: {_label(user.email, 254)}\nSigned up: {user.date_joined.date()}\n{model_usage(arguments, user)}"
