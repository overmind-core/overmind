from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from uuid import UUID

from django.db import transaction
from django.db.models import Count, F, OuterRef, Q, Subquery
from rest_framework.exceptions import APIException, ValidationError

from overbae.api.otlp import enqueue_trace_scoring
from overbae.models import (
    Capability,
    ConnectorCredential,
    ConnectorGroupReview,
    ConnectorTraceGroup,
    ScoringPass,
    Span,
    TaskExecution,
    Verdict,
)


class ReviewConflict(APIException):
    status_code = 409
    default_detail = "This group changed. Refresh it before assigning traces."


def value_shape(value, depth=0):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return "text"
    if depth >= 4:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): value_shape(v, depth + 1) for k, v in sorted(value.items())}
    if isinstance(value, list):
        return sorted({json.dumps(value_shape(v, depth + 1), sort_keys=True) for v in value})
    return type(value).__name__


def trace_signature(spans):
    by_id = {s.span_id: s for s in spans}
    roots = [s for s in spans if s.parent_span_id is None]
    root = min(roots or spans, key=lambda s: (s.start_time_ns, s.span_id))
    paths, templates = [], set()
    for span in spans:
        parent = by_id.get(span.parent_span_id)
        attrs = span.attributes or {}
        paths.append(
            (
                span.name,
                span.span_type,
                parent.name if parent else "",
                value_shape(attrs.get("overmind.input.data")),
                value_shape(attrs.get("overmind.output.data")),
            )
        )
        payload = attrs.get("overmind.input.data")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                payload = None
        messages = payload.get("messages", []) if isinstance(payload, dict) else payload
        if isinstance(messages, list):
            for message in messages:
                if isinstance(message, dict) and message.get("role") in {"system", "developer"}:
                    templates.add(
                        hashlib.sha256(
                            json.dumps(message.get("content"), sort_keys=True).encode()
                        ).hexdigest()
                    )
        for key, value in attrs.items():
            if key.endswith(("prompt_id", "prompt_name", "prompt_version")):
                templates.add(f"{key}:{value}")
    tools = sorted({s.name for s in spans if s.span_type == "tool_call"})
    payload = {
        "paths": sorted(paths, key=lambda p: json.dumps(p, sort_keys=True)),
        "templates": sorted(templates),
    }
    incomplete = not roots or any(s.parent_span_id and s.parent_span_id not in by_id for s in spans)
    # Generic, uninstrumented calls provide no shared task identity.
    if incomplete or (
        not tools
        and not templates
        and root.name.lower() in {"run", "root", "agent", "span", "generation", "chat", "llm"}
    ):
        payload["trace"] = root.trace_id
    fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return (
        fingerprint,
        root.name or "Unnamed trace",
        {
            "tools": tools,
            "span_count": len(spans),
            "root_count": len(roots),
            "input_shape": value_shape((root.attributes or {}).get("overmind.input.data")),
            "output_shape": value_shape((root.attributes or {}).get("overmind.output.data")),
            "incomplete": incomplete,
            "agent_steps": sum(
                any(
                    k.endswith(".observation_type") and str(v).upper() == "AGENT"
                    for k, v in (s.attributes or {}).items()
                )
                for s in spans
            ),
        },
    )


def group_imported_traces(credential, project, trace_ids):
    roots = []
    # The importer and reviewer both hold the credential lock while changing membership.
    for trace_id in sorted(set(trace_ids)):
        spans = list(Span.objects.filter(project=project, trace_id=trace_id))
        if not spans:
            continue
        fingerprint, name, evidence = trace_signature(spans)
        group, created = ConnectorTraceGroup.objects.get_or_create(
            credential=credential,
            project=project,
            fingerprint=fingerprint,
            defaults={"name": name, "evidence": evidence},
        )
        old_ids = {
            s.connector_group_id
            for s in spans
            if s.connector_group_id and s.connector_group_id != group.id
        }
        changed = any(s.connector_group_id != group.id for s in spans)
        if changed:
            Span.objects.filter(pk__in=[s.pk for s in spans]).update(connector_group=group)
            if not created:
                old_ids.add(group.id)
            ConnectorTraceGroup.objects.filter(pk__in=old_ids).update(revision=F("revision") + 1)
        review = group.reviews.first()
        capability = None
        if review and review.capability_id_snapshot:
            capability = Capability.objects.filter(
                pk=review.capability_id_snapshot, project=project, status="current"
            ).first()
        approved = review is not None and (review.capability_id_snapshot is None or capability)
        if approved and (changed or any(not s.connector_reviewed for s in spans)):
            with ExitStack() as locks:
                roots.extend(assign_spans(group, spans, capability, locks=locks))
        elif changed and old_ids:
            # A changed structure has left its approved pattern.
            with ExitStack() as locks:
                assign_spans(group, spans, None, locks=locks, reviewed=False)
    transaction.on_commit(lambda: enqueue_trace_scoring(roots))


def groups_with_review_state(credential):
    latest = ConnectorGroupReview.objects.filter(group=OuterRef("pk"))
    return (
        credential.trace_groups.filter(project_id=credential.project_id)
        .annotate(
            trace_count=Count("spans__trace_id", distinct=True),
            pending_count=Count(
                "spans__trace_id", filter=Q(spans__connector_reviewed=False), distinct=True
            ),
            approved_at=Subquery(latest.values("created_at")[:1]),
            approved_capability=Subquery(latest.values("capability_id_snapshot")[:1]),
        )
        .filter(trace_count__gt=0)
    )


def pending_groups(credential):
    valid_capabilities = Capability.objects.filter(
        project_id=credential.project_id, status="current"
    )
    return groups_with_review_state(credential).filter(
        Q(pending_count__gt=0)
        | Q(approved_at__isnull=True)
        | (
            Q(approved_capability__isnull=False)
            & ~Q(approved_capability__in=valid_capabilities.values("id"))
        )
    )


def review_summary(credential):
    groups = list(
        pending_groups(credential).values(
            "trace_count", "pending_count", "approved_at", "approved_capability"
        )
    )
    current = set(
        Capability.objects.filter(project_id=credential.project_id, status="current").values_list(
            "id", flat=True
        )
    )
    return {
        "pending_groups": len(groups),
        "pending_traces": sum(
            g["trace_count"]
            if not g["approved_at"]
            or (g["approved_capability"] and g["approved_capability"] not in current)
            else g["pending_count"]
            for g in groups
        ),
    }


def group_payload(group):
    spans = group.spans.all()
    stats = spans.aggregate(
        trace_count=Count("trace_id", distinct=True),
        unreviewed_trace_count=Count("trace_id", filter=Q(connector_reviewed=False), distinct=True),
    )
    capabilities = list(spans.order_by().values_list("capability_id", flat=True).distinct())
    samples = list(spans.order_by("trace_id").values_list("trace_id", flat=True).distinct()[:3])
    review = group.reviews.first()
    rule_valid = review is not None and (
        review.capability_id_snapshot is None
        or Capability.objects.filter(
            pk=review.capability_id_snapshot, project_id=group.project_id, status="current"
        ).exists()
    )
    return {
        "id": str(group.id),
        "name": group.name,
        "revision": group.revision,
        "needs_review": bool(stats["unreviewed_trace_count"] or not rule_valid),
        **stats,
        "evidence": group.evidence,
        "sample_trace_ids": samples,
        "capability_id": str(capabilities[0])
        if len(capabilities) == 1 and capabilities[0]
        else None,
        "mixed": len(capabilities) > 1,
        "last_reviewed_at": review.created_at if review else None,
        "last_reviewed_trace_count": review.trace_count if review else None,
    }


def list_groups(credential):
    return [
        group_payload(g)
        for g in credential.trace_groups.filter(
            project_id=credential.project_id, spans__isnull=False
        )
        .distinct()
        .prefetch_related("reviews")
    ]


def assign_spans(group, spans, capability, *, locks, reviewed=True):
    trace_ids = sorted({s.trace_id for s in spans})
    # Scoring writes several independent tables; share its lease to avoid stale results landing.
    from overbae.tasks.trace_scoring import single_flight  # breaks the task/service import cycle

    for trace_id in trace_ids:
        if not locks.enter_context(single_flight(trace_id, str(group.project_id))):
            raise ReviewConflict("Some traces are being scored. Try this assignment again shortly.")
    changed = {
        s.trace_id
        for s in spans
        if not s.connector_reviewed or s.capability_id != (capability.id if capability else None)
    }
    for span in spans:
        attrs = dict(span.attributes or {})
        attrs.pop("overmind.capability.name", None)
        attrs.pop("overmind.capability.id", None)
        if capability:
            attrs["overmind.capability.id"] = str(capability.id)
        span.attributes = attrs
        span.capability = capability
        span.connector_reviewed = reviewed
        if span.trace_id in changed:
            span.feedback_score = None
    Span.objects.bulk_update(
        spans,
        ["attributes", "capability", "connector_reviewed", "feedback_score"],
        batch_size=500,
    )
    TaskExecution.objects.filter(project=group.project, trace_id__in=changed).delete()
    ScoringPass.objects.filter(project=group.project, trace_id__in=changed).delete()
    Verdict.objects.filter(project=group.project).filter(
        Q(target_kind="trace", target_id__in=changed)
        | Q(target_kind="span", target_id__in=[s.pk for s in spans if s.trace_id in changed])
    ).delete()
    return [s for s in spans if not s.parent_span_id and s.trace_id in changed and capability]


def apply_group_review(
    credential, group_id, *, capability_id, expected_revision, actor=None, locks
):
    # Serializes reviews with incoming pages, including a trace joining an existing group.
    ConnectorCredential.objects.select_for_update().get(pk=credential.pk)
    group = (
        ConnectorTraceGroup.objects.filter(
            pk=group_id, credential=credential, project_id=credential.project_id
        )
        .select_related("project")
        .first()
    )
    if group is None:
        raise ValidationError("The trace group was not found.")
    if group.revision != expected_revision:
        raise ReviewConflict()
    capability = None
    if capability_id is not None:
        capability = Capability.objects.filter(
            pk=capability_id, project=group.project, status="current"
        ).first()
        if capability is None:
            raise ValidationError("Select a current capability in this project.")
    spans = list(group.spans.all())
    trace_ids = sorted({s.trace_id for s in spans})
    if not spans:
        raise ReviewConflict()
    roots = assign_spans(group, spans, capability, locks=locks)
    ConnectorGroupReview.objects.create(
        group=group,
        actor=actor,
        capability_id_snapshot=capability.id if capability else None,
        capability_name=capability.name if capability else "",
        revision=group.revision,
        trace_count=len(trace_ids),
    )
    group.revision += 1
    group.save(update_fields=["revision", "updated_at"])
    return group_payload(group), roots


def review_groups(credential, assignments, *, actor=None):
    if not assignments or len({str(a["group_id"]) for a in assignments}) != len(assignments):
        raise ValidationError("Review each group once.")
    results, roots = [], []
    with ExitStack() as locks, transaction.atomic():
        for assignment in sorted(assignments, key=lambda a: str(a["group_id"])):
            try:
                group_id = UUID(str(assignment["group_id"]))
                capability_id = assignment["capability_id"]
                if capability_id is not None:
                    capability_id = UUID(str(capability_id))
            except ValueError as exc:
                raise ValidationError("Select a valid trace group and capability.") from exc
            result, group_roots = apply_group_review(
                credential,
                group_id,
                capability_id=capability_id,
                expected_revision=assignment["expected_revision"],
                actor=actor,
                locks=locks,
            )
            results.append(result)
            roots.extend(group_roots)
    transaction.on_commit(lambda: enqueue_trace_scoring(roots))
    return results


def review_group(credential, group_id, *, capability_id, expected_revision, actor=None):
    return review_groups(
        credential,
        [
            {
                "group_id": group_id,
                "capability_id": capability_id,
                "expected_revision": expected_revision,
            }
        ],
        actor=actor,
    )[0]
