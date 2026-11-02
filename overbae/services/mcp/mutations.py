import hashlib
import json

from django.db import transaction
from django.utils import timezone

from overbae.models import MCPMutation
from overbae.services.mcp.errors import MCPError


def claim(context, tool, request_key, payload):
    fingerprint = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with transaction.atomic():
        receipt, created = MCPMutation.objects.get_or_create(
            project=context.project,
            request_key=request_key,
            defaults={"user": context.user, "tool": tool, "fingerprint": fingerprint},
        )
        if receipt.user_id != context.user.pk:
            raise MCPError("permission_denied", "This request identity belongs to another account.")
        if receipt.tool != tool or receipt.fingerprint != fingerprint:
            raise MCPError(
                "request_key_conflict",
                "This request key already identifies a different operation. Inspect the original receipt or use a new key for new work.",
            )
        if not created and receipt.state != "completed":
            raise MCPError(
                "request_in_progress",
                "The original operation may have executed. Inspect its receipt and related jobs; do not resubmit with a new key while acknowledgement is unresolved.",
                fields={"receipt": str(receipt.pk), "state": receipt.state},
            )
        return receipt, created


def finish(receipt, result):
    MCPMutation.objects.filter(pk=receipt.pk).update(
        state="completed", result=result, updated_at=timezone.now()
    )


def release(receipt):
    MCPMutation.objects.filter(pk=receipt.pk).delete()


def mark_unknown(receipt):
    MCPMutation.objects.filter(pk=receipt.pk).update(state="unknown", updated_at=timezone.now())


def inspect(context, request_key):
    receipt = MCPMutation.objects.filter(
        project=context.project, user=context.user, request_key=request_key
    ).first()
    if receipt is None:
        raise MCPError(
            "resource_not_found",
            "No mutation receipt exists for this account, project and request key.",
            next_actions=[],
        )
    return {
        "id": str(receipt.pk),
        "tool": receipt.tool,
        "state": receipt.state,
        "created_at": receipt.created_at,
        "updated_at": receipt.updated_at,
        "result": receipt.result,
        "guidance": "Completed receipts recover the original response. Pending or unknown receipts must be reconciled with discovered jobs before any new submission. Native request-key tools retain their own workflow receipts.",
    }
