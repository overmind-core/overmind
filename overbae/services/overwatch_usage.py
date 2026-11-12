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


def _usage(project_ids, days, now, details):
    since = window_start(days)
    calls = InferenceCall.objects.filter(project_id__in=project_ids, created_at__lte=now)
    current = calls.filter(created_at__gte=since)
    totals = _totals(current)
    previous = _totals(
        calls.filter(
            created_at__gte=since - timedelta(days=days), created_at__lte=now - timedelta(days=days)
        )
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
    models = (
        list(
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
        if details
        else []
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
    totals, previous, sources, models, last = _usage(
        project_ids, days, now, arguments.get("view") == "details"
    )
    tokens = totals["input_tokens"] + totals["output_tokens"]
    label = _label(user.get_full_name() or user.email, 70) if user else "Model usage"
    lines = [f"{label} · {since:%d %b}–{now:%d %b %Y} (UTC, today partial)"]
    if totals["calls"]:
        lines.extend(
            [
                f"{totals['succeeded']:,} successful calls across {totals['projects']} active projects.",
                f"• {_compact(tokens)} recorded tokens · {totals['active_days']} active days",
                f"• {100 * totals['succeeded'] / totals['calls']:.1f}% success · {totals['calls'] - totals['succeeded']:,} unsuccessful calls",
                f"• {_change(totals['succeeded'], previous['succeeded'])}",
            ]
        )
        if not tokens:
            lines.append("Token counts are unavailable or zero in the retained records.")
    else:
        lines.append("No hosted-model calls recorded in this period (0 successful).")
        if previous["succeeded"]:
            lines.append(_change(0, previous["succeeded"]))
    if totals["succeeded"] and sources:
        dominant = max(sources, key=lambda row: row["succeeded"])
        share = dominant["succeeded"] / totals["succeeded"] * 100
        if share >= 50:
            origin = {
                "application": "API",
                "playground": "Playground",
                "internal": "Internal",
                "unknown": "Unknown-source",
            }[dominant["origin"]]
            lines.append(f"{origin} traffic accounts for {share:.0f}% of successful calls.")
    lines.append(f"Last successful call: {_when(last)}.")
    if user:
        lines.append("Shared-project activity; individual caller attribution is unavailable.")
    lines.append("Recorded hosted-model activity only; ask ‘show details’ for sources and models.")
    if arguments.get("view", "snapshot") != "details":
        return "\n".join(lines)
    lines.extend(
        [
            f"Recorded tokens: {totals['input_tokens']:,} input / {totals['output_tokens']:,} output (all outcomes).",
            f"Previous matched period: {previous['succeeded']:,} successful calls; {previous['input_tokens'] + previous['output_tokens']:,} recorded tokens.",
        ]
    )
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
            if len("\n".join(lines)) + len(line) > 2400:
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


def _compact(value):
    if value >= 1_000_000:
        return f"{value / 1_000_000:.2f}m"
    if value >= 10_000:
        return f"{value / 1000:.1f}k"
    return f"{value:,}"


def _when(value):
    return value.strftime("%d %b %Y, %H:%M UTC") if value else "none recorded"


def _change(current, previous):
    if not previous:
        return "No successful calls in the matched previous period; no percentage comparison."
    delta = (current - previous) / previous * 100
    if not delta:
        return "Successful calls unchanged from the matched previous period."
    return f"Successful calls {'up' if delta > 0 else 'down'} {abs(delta):.0f}% vs matched previous period ({previous:,})."


def project_activity(arguments):
    """Rank project facts once, independently of how many customers share them."""
    days, now = arguments["days"], timezone.now()
    since = window_start(days)
    previous_end = now - timedelta(days=days)
    current = Q(created_at__gte=since)
    previous = Q(created_at__lt=since, created_at__lte=previous_end)
    succeeded = Q(outcome="succeeded")
    project_ids = ProjectMembership.objects.filter(user__is_guest=False).values("project_id")
    rows = (
        InferenceCall.objects.filter(
            project_id__in=project_ids,
            created_at__gte=since - timedelta(days=days),
            created_at__lte=now,
        )
        .order_by()
        .values("project_id", "project__name")
        .annotate(
            successful=Count("pk", filter=current & succeeded),
            previous=Count("pk", filter=previous & succeeded),
            tokens=Sum(F("prompt_tokens") + F("completion_tokens"), filter=current, default=0),
            last=Max("created_at", filter=current & succeeded),
        )
        .annotate(change=F("successful") - F("previous"))
    )
    rank = arguments.get("rank_by", "calls")
    if rank == "growth":
        rows = rows.filter(change__gt=0).order_by("-change", "project_id")
    elif rank == "decline":
        rows = rows.filter(change__lt=0).order_by("change", "project_id")
    elif rank == "tokens":
        rows = rows.filter(tokens__gt=0).order_by("-tokens", "project_id")
    else:
        rows = rows.filter(successful__gt=0).order_by("-successful", "project_id")
    detail = arguments.get("view", "snapshot") == "details"
    limit = 5 if detail else 3
    found = list(rows[: limit + 1])
    title = {
        "calls": "Most active projects",
        "tokens": "Projects using the most tokens",
        "growth": "Projects with growing usage",
        "decline": "Projects with declining usage",
    }[rank]
    lines = [f"{title} · {since:%d %b}–{now:%d %b %Y} (UTC, today partial)"]
    lines.append("Project activity is measurable; individual user rankings are unavailable.")
    if not found:
        lines.append("No projects match this measure in the retained hosted-model records.")
    for index, row in enumerate(found[:limit], 1):
        lines.append(
            f"{index}. {_label(row['project__name'], 60)} · {row['successful']:,} successful calls · {_compact(row['tokens'])} recorded tokens"
        )
        members = list(
            ProjectMembership.objects.filter(project_id=row["project_id"], user__is_guest=False)
            .select_related("user")
            .order_by("user__email")[:3]
        )
        names = ", ".join(
            _label(member.user.get_full_name() or member.user.email, 60) for member in members[:2]
        )
        lines.append(
            f"Members: {names}{' + others' if len(members) > 2 else ''} (not identified callers)."
        )
        if rank in {"growth", "decline"} or detail:
            lines.append(_change(row["successful"], row["previous"]))
        if detail:
            lines.append(f"Last successful call in period: {_when(row['last'])}.")
    if len(found) > limit:
        lines.append(f"Showing the top {limit}; more projects match.")
    measure = (
        "recorded tokens (all outcomes)"
        if rank == "tokens"
        else "successful calls"
        if rank == "calls"
        else "absolute change in successful calls vs matched previous period"
    )
    lines.append(
        f"Ranked by {measure}; all sources, hosted models only. Zero tokens may mean missing telemetry."
    )
    if not detail:
        lines.append("Ask ‘show details’, ‘who is growing?’ or ‘who is dropping off?’.")
    return "\n".join(lines)
