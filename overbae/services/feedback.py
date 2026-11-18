"""Feedback from people in the Console and friction reports from their coding agents."""

from __future__ import annotations

import hashlib
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from overbae.models import Feedback, Project, User
from overbae.services.overwatch_privacy import report_text

AGENT_REPORTS_PER_HOUR = 20
SEEN_WINDOW = timedelta(days=7)
# A repeat of a problem already posted in this window adds to its count, not to the channel.
QUIET_WINDOW = timedelta(hours=24)


class FeedbackError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def agent_reports_allowed(user: User) -> bool:
    return user.agent_feedback is True and not user.is_guest


def set_agent_feedback(user: User, allowed: bool) -> User:
    user.agent_feedback = allowed
    user.agent_feedback_at = timezone.now()
    user.save(update_fields=["agent_feedback", "agent_feedback_at"])
    return user


def title_from(message: str) -> str:
    line = next((line.strip() for line in message.splitlines() if line.strip()), "Feedback")
    return line if len(line) <= 120 else line[:119] + "…"


def fingerprint(source: str, kind: str, area: str, step: str = "", code: str = "") -> str:
    return hashlib.sha256(f"{source}:{kind}:{area}:{step}:{code}".encode()).hexdigest()


def record_console_feedback(
    *, user: User | None, project: Project | None, kind: str, area: str, message: str, page: str
) -> Feedback:
    feedback = Feedback.objects.create(
        source=Feedback.Source.CONSOLE,
        user=user,
        project=project,
        kind=kind,
        area=area,
        title=title_from(message),
        message=message,
        environment={"observed": {"page": page}} if page else {},
        fingerprint=fingerprint(Feedback.Source.CONSOLE, kind, area),
    )
    _notify(feedback)
    return feedback


def record_agent_report(
    *,
    user: User,
    project: Project | None,
    report_id,
    report: dict,
    reported: dict,
    observed: dict,
) -> tuple[Feedback, bool]:
    """Save a coding agent's friction report; a resend with the same id updates it."""
    if not agent_reports_allowed(user):
        raise FeedbackError("permission_denied", "Agent feedback is off for this account.")
    reported = {
        key: report_text(value) if isinstance(value, str) else value
        for key, value in reported.items()
    }
    text = {
        key: report_text(report[key])
        for key in ("headline", "summary", "intent", "expected", "observed", "suggestion")
        if report.get(key)
    }
    trail = [
        {**step, "note": report_text(step["note"])} if step.get("note") else step
        for step in report.get("trail", [])
    ]
    failed = next((step for step in trail if step["result"] != "ok"), {})
    fields = {
        "source": Feedback.Source.AGENT,
        "user": user,
        "project": project,
        "kind": report["kind"],
        "area": report["area"],
        "severity": report["severity"],
        "outcome": report["outcome"],
        "title": text["headline"],
        "message": text["summary"],
        "detail": {
            **{
                key: text[key]
                for key in ("intent", "expected", "observed", "suggestion")
                if key in text
            },
            "trail": trail,
            "resources": report.get("resources", []),
            "tool_calls": report.get("tool_calls"),
        },
        "environment": {"reported": reported, "observed": observed},
        "fingerprint": fingerprint(
            Feedback.Source.AGENT,
            report["kind"],
            report["area"],
            failed.get("action", ""),
            failed.get("error_code", ""),
        ),
    }
    with transaction.atomic():
        existing = Feedback.objects.select_for_update().filter(pk=report_id).first()
        if existing is not None:
            if existing.user_id != user.pk or existing.source != Feedback.Source.AGENT:
                raise FeedbackError(
                    "request_key_conflict", "This report_id belongs to another report."
                )
            for name, value in fields.items():
                setattr(existing, name, value)
            existing.save()
            return existing, False
        recent = Feedback.objects.filter(
            user=user,
            source=Feedback.Source.AGENT,
            created_at__gte=timezone.now() - timedelta(hours=1),
        ).count()
        if recent >= AGENT_REPORTS_PER_HOUR:
            raise FeedbackError(
                "rate_limited",
                f"This account sent {AGENT_REPORTS_PER_HOUR} reports in the last hour.",
            )
        feedback = Feedback.objects.create(id=report_id, **fields)
    _notify(feedback)
    return feedback, True


def _notify(feedback: Feedback) -> None:
    if not settings.SLACK_FEEDBACK_WEBHOOK_URL:
        return
    from overbae.tasks.feedback import post_to_slack  # Lazy: tasks import this module.

    transaction.on_commit(lambda: post_to_slack.delay(str(feedback.pk)))


def seen(feedback: Feedback) -> int:
    """Reports of the same problem in the last week, this one included."""
    return Feedback.objects.filter(
        fingerprint=feedback.fingerprint,
        created_at__gte=feedback.created_at - SEEN_WINDOW,
        created_at__lte=feedback.created_at,
    ).count()


def posted_recently(feedback: Feedback) -> bool:
    return (
        feedback.source == Feedback.Source.AGENT
        and Feedback.objects.filter(
            fingerprint=feedback.fingerprint,
            created_at__gte=feedback.created_at - QUIET_WINDOW,
            created_at__lt=feedback.created_at,
        ).exists()
    )


SEVERITY_MARK = {"blocked": "🔴", "degraded": "🟠", "minor": "🟡"}
KIND_MARK = {"praise": "💚", "idea": "💡", "question": "❔"}
RESULT_MARK = {"ok": "✓", "error": "✗", "unexpected": "!", "no_progress": "↻"}


def slack_message(feedback: Feedback) -> dict:
    """Block Kit: what broke, what the agent was doing, how it got there, then who and where."""
    mark = SEVERITY_MARK.get(feedback.severity) or KIND_MARK.get(feedback.kind, "💬")
    status = [
        feedback.get_severity_display() if feedback.severity else "",
        feedback.get_kind_display(),
        feedback.get_area_display(),
        feedback.get_outcome_display() if feedback.outcome else "",
    ]
    detail = feedback.detail or {}
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": feedback.title[:150]}},
        _context(f"{mark} *{' · '.join(part for part in status if part)}*"),
    ]
    if detail.get("intent"):
        blocks.append(_section(f"*The agent was trying to*\n{_clip(detail['intent'], 300)}"))
    label = "What happened" if feedback.source == Feedback.Source.AGENT else "Message"
    blocks.append(_section(f"*{label}*\n{_clip(feedback.message, 2800)}"))
    if detail.get("expected") or detail.get("observed"):
        blocks.append(
            {
                "type": "section",
                "fields": [
                    _mrkdwn(f"*Expected*\n{_clip(detail.get('expected') or '—', 1900)}"),
                    _mrkdwn(f"*Observed*\n{_clip(detail.get('observed') or '—', 1900)}"),
                ],
            }
        )
    if detail.get("trail"):
        blocks.append(_section(f"*Trail*\n```{_trail(detail['trail'])}```"))
    if detail.get("suggestion"):
        blocks.append(_section(f"*Suggested fix*\n{_clip(detail['suggestion'], 2800)}"))
    blocks.append({"type": "divider"})
    environment = _environment_line(feedback)
    if environment:
        blocks.append(_context(environment))
    blocks.append(_context(_who_line(feedback)))
    return {"text": f"{mark} {_clip(feedback.title, 150)}", "blocks": blocks}


def _trail(steps: list[dict]) -> str:
    width = max(len(step["action"]) for step in steps)
    lines = []
    for index, step in enumerate(steps, 1):
        line = f"{index:>2} {RESULT_MARK[step['result']]} {step['action']:<{width}}"
        extra = [step.get("error_code") or ""]
        if step.get("attempts", 1) > 1:
            extra.append(f"×{step['attempts']}")
        if step.get("note"):
            extra.append(step["note"])
        lines.append(f"{line}  {' '.join(part for part in extra if part)}".rstrip())
    return _clip("\n".join(lines), 2800)


def _environment_line(feedback: Feedback) -> str:
    environment = feedback.environment or {}
    reported = environment.get("reported", {})
    observed = environment.get("observed", {})
    harness = " ".join(
        part
        for part in (
            reported.get("harness") or observed.get("client"),
            reported.get("harness_version"),
        )
        if part
    )
    os_name = " ".join(part for part in (reported.get("os"), reported.get("os_version")) if part)
    parts = [
        harness,
        reported.get("model"),
        os_name,
        f"CLI {reported['overmind_cli']}" if reported.get("overmind_cli") else "",
        f"skills {reported['skills_version']}" if reported.get("skills_version") else "",
        f"contract {observed['contract_version']}" if observed.get("contract_version") else "",
        observed.get("page", ""),
    ]
    return " · ".join(f"`{_clip(part, 80)}`" for part in parts if part)


def _who_line(feedback: Feedback) -> str:
    count = seen(feedback)
    parts = [
        f"User {feedback.user_id}" if feedback.user_id else "Anonymous",
        f"Project `{str(feedback.project_id)[:8]}`" if feedback.project_id else "No project",
    ]
    tool_calls = (feedback.detail or {}).get("tool_calls")
    if tool_calls is not None:
        parts.append(f"{tool_calls} tool calls")
    parts.append(f"Seen {count}× in 7 days" if count > 1 else "First report in 7 days")
    if settings.OVERWATCH_URL:
        parts.append(
            f"<{settings.OVERWATCH_URL.rstrip('/')}/feedback/{feedback.pk}|Open in Overwatch>"
        )
    return " · ".join(parts)


def _clip(text: str, limit: int) -> str:
    # Escaped so report text can never mention a person or a channel.
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _mrkdwn(text: str) -> dict:
    return {"type": "mrkdwn", "text": text}


def _section(text: str) -> dict:
    return {"type": "section", "text": _mrkdwn(text)}


def _context(text: str) -> dict:
    return {"type": "context", "elements": [_mrkdwn(text)]}
