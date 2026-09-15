"""Default eval-set preload lifecycle, stored on ``Capability.improvement_metadata``
because the frontend reads it off capability detail — there is no serializer field."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from django.utils import timezone

if TYPE_CHECKING:
    from overbae.models import Capability

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_READY = "ready"
STATUS_EMPTY = "empty"
STATUS_FAILED = "failed"

ALL_STATUSES = frozenset(
    {
        STATUS_PENDING,
        STATUS_RUNNING,
        STATUS_READY,
        STATUS_EMPTY,
        STATUS_FAILED,
    }
)

TERMINAL_STATUSES = frozenset({STATUS_READY, STATUS_EMPTY, STATUS_FAILED})

ACTIVE_STATUSES = frozenset({STATUS_PENDING, STATUS_RUNNING})


def is_active_status(status: str | None) -> bool:
    return status in ACTIVE_STATUSES


def _capability_metadata(capability: Capability) -> dict[str, Any]:
    meta = capability.improvement_metadata
    return dict(meta) if isinstance(meta, dict) else {}


def read_eval_preload(capability: Capability) -> dict[str, Any] | None:
    meta = capability.improvement_metadata
    if not isinstance(meta, dict):
        return None
    blob = meta.get("eval_preload")
    if not isinstance(blob, dict):
        return None
    if blob.get("status") not in ALL_STATUSES:
        return None
    return dict(blob)


def terminal_status_from_result(result: dict[str, Any]) -> str:
    if result.get("generated", 0) == 0:
        return STATUS_EMPTY
    return STATUS_READY


def preload_counts_from_result(result: dict[str, Any]) -> dict[str, Any]:
    keys = ("generated", "created", "added", "generative", "trace_scoring")
    return {k: result[k] for k in keys if k in result}


def write_eval_preload(
    capability: Capability,
    *,
    status: str,
    error: str | None = None,
    counts: dict[str, Any] | None = None,
    save: bool = True,
) -> dict[str, Any]:
    if status not in ALL_STATUSES:
        raise ValueError(f"invalid eval_preload status: {status!r}")

    meta = _capability_metadata(capability)
    prior = meta.get("eval_preload")
    blob: dict[str, Any] = dict(prior) if isinstance(prior, dict) else {}

    now = timezone.now().isoformat()
    if status in ACTIVE_STATUSES and not blob.get("started_at"):
        blob["started_at"] = now

    blob["status"] = status

    if status in TERMINAL_STATUSES:
        blob["finished_at"] = now
    else:
        blob.pop("finished_at", None)

    if error is not None:
        blob["error"] = error
    elif status != STATUS_FAILED:
        blob.pop("error", None)

    if counts is not None:
        blob["counts"] = counts

    meta["eval_preload"] = blob
    capability.improvement_metadata = meta

    if save:
        capability.save(update_fields=["improvement_metadata"])

    return blob
