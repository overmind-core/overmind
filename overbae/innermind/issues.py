"""Records what checks find as issues. Reads live in ``services.issue_overview``."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from overbae.models import Issue, IssueTrace

# An issue silent this long counts as resolved; its next occurrence is a regression.
RESOLVE_AFTER = timedelta(days=14)


@dataclass
class IssueOccurrence:
    fingerprint: str
    family: str
    title: str
    cause: str
    capability_id: Any
    trace_id: str
    span_id: str
    conversation_id: Any
    signal_type: str
    reason: str
    occurred_at: datetime


def record_occurrences(project_id, occurrences: list[IssueOccurrence]) -> None:
    """Idempotent: checks re-read the same day of spans, so a known trace changes nothing."""
    if not occurrences:
        return
    by_fingerprint = defaultdict(list)
    for occurrence in sorted(occurrences, key=lambda item: item.occurred_at):
        by_fingerprint[occurrence.fingerprint].append(occurrence)
    Issue.objects.bulk_create(
        [
            Issue(
                project_id=project_id,
                capability_id=found[0].capability_id,
                fingerprint=fingerprint,
                family=found[0].family,
                cause=found[0].cause,
                signal_type=found[0].signal_type,
                title=found[0].title,
                first_seen_at=found[0].occurred_at,
                last_seen_at=found[0].occurred_at,
            )
            for fingerprint, found in by_fingerprint.items()
        ],
        ignore_conflicts=True,
    )
    issues = {
        issue.fingerprint: issue
        for issue in Issue.objects.filter(project_id=project_id, fingerprint__in=by_fingerprint)
    }
    known = set(
        IssueTrace.objects.filter(
            issue__in=issues.values(), trace_id__in={item.trace_id for item in occurrences}
        ).values_list("issue__fingerprint", "trace_id")
    )
    rows = []
    for fingerprint, found in by_fingerprint.items():
        issue = issues[fingerprint]
        for occurrence in found:
            if (fingerprint, occurrence.trace_id) in known:
                continue
            known.add((fingerprint, occurrence.trace_id))
            rows.append(
                IssueTrace(
                    issue=issue,
                    project_id=project_id,
                    trace_id=occurrence.trace_id,
                    span_id=occurrence.span_id,
                    conversation_id=occurrence.conversation_id,
                    signal_type=occurrence.signal_type,
                    reason=occurrence.reason,
                    occurred_at=occurrence.occurred_at,
                )
            )
            when = occurrence.occurred_at
            if when > issue.last_seen_at:
                resolved = issue.resolved_at and when > issue.resolved_at
                if resolved or when - issue.last_seen_at >= RESOLVE_AFTER:
                    issue.regressed_at = when
                    issue.resolved_at = None
                issue.last_seen_at = when
            issue.first_seen_at = min(issue.first_seen_at, when)
    IssueTrace.objects.bulk_create(rows, ignore_conflicts=True)
    Issue.objects.bulk_update(
        issues.values(), ["first_seen_at", "last_seen_at", "resolved_at", "regressed_at"]
    )
