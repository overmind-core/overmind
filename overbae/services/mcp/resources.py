"""Project-scoped MCP resources backed by existing Overmind rows."""

from __future__ import annotations

import json
import uuid
from collections import Counter
from collections.abc import Iterable
from urllib.parse import quote, unquote, urlparse

from asgiref.sync import sync_to_async
from django.db.models import Count, Prefetch, Sum
from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import McpError
from pydantic import AnyUrl

from overbae.api.eval_serializers import compute_run_progress
from overbae.models import (
    Capability,
    Cell,
    Dataset,
    DeployedModel,
    FinetuningJob,
    OptimizerCandidate,
    OptimizerExperiment,
    Span,
)
from overbae.services.entity_resolution import (
    resolve_capability,
    resolve_connector,
    resolve_dataset,
    resolve_eval_run,
    resolve_session,
)
from overbae.services.mcp.context import get_context
from overbae.services.mcp.contracts.datasets import serialize_dataset_detail
from overbae.services.mcp.contracts.instrumentation import MAX_INSTRUMENTATION_SPANS
from overbae.services.mcp.errors import MCPError, error_payload, internal_error

JSON_MIME = "application/json"
_MAX_EVENTS = 20
_MAX_LIST = 50
_OPTIMIZER_PATCH_CAP = 4_000
_CHAT_DEFAULT = 10
_ERROR_CAP = 1_000
_CHAT_TEXT_CAP = 2_000


def _normalize_key(key: object) -> str:
    return "".join(character for character in str(key).casefold() if character.isalnum())


def _contains_key_part(key: object, parts: Iterable[str]) -> bool:
    normalized = _normalize_key(key)
    return any(part in normalized for part in parts)


_SENSITIVE_PARTS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "cookie",
        "credential",
        "password",
        "private_key",
        "refresh_token",
        "secret",
        "token",
    }
)
_SENSITIVE_COMPACT_PARTS = frozenset(_normalize_key(part) for part in _SENSITIVE_PARTS)
_FINETUNE_PROGRESS_BLOCKED_PARTS = frozenset(
    {"artifact", "artifacts", "checkpoint", "checkpoints", "download", "presigned", "uri", "url"}
)
_FINETUNE_PROGRESS_BLOCKED_COMPACT_PARTS = frozenset(
    _normalize_key(part) for part in _FINETUNE_PROGRESS_BLOCKED_PARTS
)
_FINETUNE_PROGRESS_REDACTED_PARTS = (
    _SENSITIVE_COMPACT_PARTS | _FINETUNE_PROGRESS_BLOCKED_COMPACT_PARTS
)
_CONNECTOR_MAPPING_SOURCES = frozenset({"observation_name", "metadata", "tag", "trace_name"})


def _connector_setup_resource(uri: str) -> dict:
    return {
        "uri": uri,
        "kind": "connector_setup",
        "command": "overmind connector add langfuse --json",
        "types": [
            {
                "connector_type": "langfuse",
                "auth": "pair",
                "command": "overmind connector add langfuse --json",
                "env": ["LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"],
            },
            {
                "connector_type": "langsmith",
                "auth": "bearer",
                "command": "overmind connector add langsmith --json",
                "env": ["LANGSMITH_API_KEY"],
            },
            {
                "connector_type": "braintrust",
                "auth": "bearer",
                "command": "overmind connector add braintrust --json",
                "env": ["BRAINTRUST_API_KEY"],
            },
            {
                "connector_type": "galileo",
                "auth": "bearer",
                "command": "overmind connector add galileo --json",
                "env": ["GALILEO_API_KEY"],
            },
        ],
        "auth": (
            "Overmind key from --api-key, .overmind/credentials.toml, or OVERMIND_API_KEY. "
            "project-id must be this MCP project."
        ),
        "provider_env": (
            "Provider keys come from those env names or a TTY prompt. Generic fallback: "
            "OVERMIND_CONNECTOR_API_KEY and OVERMIND_CONNECTOR_API_SECRET."
        ),
        "boundary": (
            "The human runs the CLI in a terminal. Never paste provider keys in chat. "
            "Do not have the agent export keys or run the command in a non-TTY sandbox."
        ),
        "next_mcp_calls": [
            "inspect_connectors(connector=id, include_source_projects=true)",
            "configure_connector (stage mapping.names, present alternatives, wait, confirm_mapping=true)",
            "sync_connector",
        ],
    }


def _dataset_upload_resource(uri: str) -> dict:
    from overbae.services.datasets import files

    return {
        "uri": uri,
        "kind": "dataset_upload",
        "extensions": list(files.ALLOWED_SUFFIXES),
        "max_bytes": files.MAX_UPLOAD_BYTES,
        "json_array_max_bytes": files.JSON_ARRAY_MAX_BYTES,
        "limits": (
            f"CSV, TSV, JSON, JSONL, NDJSON, and Parquet uploads are capped at "
            f"{files.MAX_UPLOAD_BYTES // 1024**3} GiB. "
            f"Monolithic JSON arrays are capped at {files.JSON_ARRAY_MAX_BYTES // 1024**2} MiB "
            "because the parser reads them whole."
        ),
        "chunk_bytes": files.CHUNK_BYTES,
        "command": "overmind dataset upload FILE --json",
        "auth": "Project-scoped API key from --api-key, .overmind/credentials.toml, or OVERMIND_API_KEY.",
        "config": (
            "project-id from overmind.toml or --project-id; base URL from OVERMIND_API_URL, "
            "--api-url, or overmind.toml."
        ),
        "flow": (
            "POST /api/uploads/, resume with PUT /api/uploads/{id}/chunk/?offset= using "
            "chunk_bytes, then POST /api/datasets/ with upload_id and project."
        ),
        "next_mcp_calls": [
            "get_job(kind=dataset_run, id=dataset_id)",
        ],
    }


def _dataset_export_resource(uri: str) -> dict:
    return {
        "uri": uri,
        "kind": "dataset_export",
        "command": "overmind dataset export DATASET --json",
        "formats": ["jsonl", "csv"],
        "dataset": "Use the dataset id supplied by MCP; the CLI does not resolve dataset names.",
        "auth": "X-Api-Key from --api-key, .overmind/credentials.toml, or OVERMIND_API_KEY.",
        "config": "Base URL from OVERMIND_API_URL, --api-url, or overmind.toml; --path selects overmind.toml.",
        "output": (
            "Without --output, the CLI uses and sanitizes the server Content-Disposition filename. "
            "It refuses to overwrite an existing local path."
        ),
        "flow": ("select traces -> land a dataset from traces -> run dataset export locally"),
        "trace_export": "Keep trace export on this workflow; there is no export_trace MCP tool.",
    }


def _checkpoint_download_resource(uri: str) -> dict:
    return {
        "uri": uri,
        "kind": "checkpoint_download",
        "command": "overmind model download-checkpoint DEPLOYMENT --json",
        "deployment": (
            "Resolve and read the deployment through the existing MCP resource/tool flow, then use "
            "the deployment id supplied by MCP. The CLI does not resolve deployment names."
        ),
        "auth": "X-Api-Key from --api-key, .overmind/credentials.toml, or OVERMIND_API_KEY.",
        "config": "Base URL from OVERMIND_API_URL, --api-url, or overmind.toml; --path selects overmind.toml.",
        "mcp_boundary": (
            "MCP carries deployment metadata and guidance, not checkpoint bytes. The presigned S3 "
            "download URL is used only by the local CLI and must never be exposed to model context."
        ),
        "availability": (
            "Only archived checkpoints for deployed models linked to fine-tuning jobs from the "
            "supported providers baseten or modal are downloadable. Deployments without a "
            "fine-tuning job, unsupported providers, and archives that are not ready are unavailable."
        ),
        "output": "Parse the JSON result and report the local path and bytes_written.",
        "overwrite": "The CLI refuses to overwrite an existing local file.",
    }


def resource_uri(kind: str, value: str) -> str:
    if kind == "project":
        return "overmind://project/current"
    if kind in {"job", "jobs"}:
        job_kind, job_id = value.split("/", 1)
        return f"overmind://jobs/{quote(job_kind, safe='')}/{quote(job_id, safe='')}"
    return f"overmind://{kind}/{quote(str(value), safe='')}"


def resource_link(kind: str, value: str, title: str) -> dict[str, str]:
    return {"uri": resource_uri(kind, value), "title": title, "mimeType": JSON_MIME}


def safe_json(value, *, max_chars: int = 12_000):
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:100]:
            key_text = str(key)
            if _contains_key_part(key_text, _SENSITIVE_COMPACT_PARTS):
                continue
            result[key_text] = safe_json(item, max_chars=max_chars)
        return result
    if isinstance(value, (list, tuple)):
        return [safe_json(item, max_chars=max_chars) for item in list(value)[:100]]
    if isinstance(value, str):
        return value[:max_chars]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:max_chars]


def _safe_finetune_progress(value):
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:100]:
            key_text = str(key)
            if _contains_key_part(key_text, _FINETUNE_PROGRESS_REDACTED_PARTS):
                continue
            result[key_text] = _safe_finetune_progress(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_safe_finetune_progress(item) for item in list(value)[:100]]
    return safe_json(value)


def _uuid_ref(value: str) -> str | None:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        return None


def _not_found(kind: str, value: str) -> MCPError:
    return MCPError("resource_not_found", f"No {kind} resource {value!r} exists in this project.")


def _connector_capabilities(connector_type: str) -> dict:
    from overbae.services.connectors import capabilities_for

    capabilities = capabilities_for(connector_type)
    return {
        "exact_count": bool(getattr(capabilities, "exact_count", False)),
        "capability_sources": [
            source
            for source in (getattr(capabilities, "capability_sources", None) or [])
            if source in _CONNECTOR_MAPPING_SOURCES
        ],
        "needs_source_project": bool(getattr(capabilities, "needs_source_project", True)),
        "retention_note": str(getattr(capabilities, "retention_note", "") or "")[:1_000],
    }


def connector_mapping_assignments(project, raw) -> tuple[dict, list[dict]]:
    payload = raw if isinstance(raw, dict) else {}
    source = payload.get("source")
    mapping = {
        "source": source if source in _CONNECTOR_MAPPING_SOURCES else None,
        "key": str(payload.get("key"))[:255] if payload.get("key") is not None else None,
        "names": [str(name)[:255] for name in (payload.get("names") or [])[:100]],
        "assignments": {},
        "fallback_capability_id": None,
    }
    assignments = payload.get("assignments") if isinstance(payload.get("assignments"), dict) else {}
    ids = [normalized for value in assignments.values() if (normalized := _uuid_ref(str(value)))]
    capabilities = {
        str(capability.id): capability
        for capability in Capability.objects.filter(project=project, id__in=ids)
    }
    details = []
    for key, value in list(assignments.items())[:100]:
        source_value = str(key)[:255]
        capability_id = str(value)[:255]
        mapping["assignments"][source_value] = capability_id
        capability = capabilities.get(capability_id)
        details.append(
            {
                "source_value": source_value,
                "capability_id": capability_id,
                "capability_name": capability.name[:255] if capability else "Unknown capability",
            }
        )
    fallback = payload.get("fallback_capability_id")
    if fallback:
        mapping["fallback_capability_id"] = str(fallback)[:255]
    return mapping, details


def _connector_mapping(project, connector) -> tuple[dict, list[dict]]:
    raw = connector.capability_mapping if isinstance(connector.capability_mapping, dict) else {}
    return connector_mapping_assignments(project, raw)


def connector_resource_payload(project, connector, uri: str) -> dict:
    """Return connector metadata without credentials or provider response text."""
    config = connector.active_config()
    mapping, assignment_details = _connector_mapping(project, connector)
    runs = list(connector.runs.order_by("-started_at")[:51])
    return {
        "uri": uri,
        "kind": "connector",
        "id": str(connector.id),
        "name": connector.name[:255],
        "connector_type": connector.connector_type,
        "status": "active" if connector.is_active else "inactive",
        "verified": connector.verified_at is not None,
        "verified_at": connector.verified_at,
        "provider_capabilities": _connector_capabilities(connector.connector_type),
        "active_config": (
            {
                "id": str(config.id),
                "version": config.version,
                "source_project_id": config.source_project_id,
                "target_project_id": str(config.target_project_id)
                if config.target_project_id
                else None,
                "lookback_days": config.lookback_days,
                "backfill_from": config.backfill_from,
                "backfill_to": config.backfill_to,
                "effective_from": config.effective_from,
            }
            if config is not None
            else None
        ),
        "capability_mapping": mapping,
        "capability_assignments": assignment_details,
        "sync": {
            "status": connector.sync_status,
            "auto_sync_enabled": connector.auto_sync_enabled,
            "poll_interval_seconds": connector.poll_interval_seconds,
            "last_synced_at": connector.last_synced_at,
            "next_poll_at": connector.next_poll_at,
            "backfill_imported": connector.backfill_imported,
            "backfill_total": connector.backfill_total,
            "total_spans_imported": connector.total_spans_imported,
            "total_traces_imported": connector.total_traces_imported,
            "has_error": bool(connector.sync_error),
        },
        "sync_runs": [
            {
                "id": str(run.id),
                "mode": run.mode,
                "status": run.status,
                "config_version": run.config_version,
                "traces_seen": run.traces_seen,
                "spans_created": run.spans_created,
                "spans_skipped": run.spans_skipped,
                "started_at": run.started_at,
                "finished_at": run.finished_at,
                "has_error": bool(run.error),
            }
            for run in runs[:50]
        ],
        "sync_runs_truncated": len(runs) > 50,
        "source_projects": [],
        "source_projects_truncated": False,
        "preview_count": None,
        "provider_status": "not_requested",
    }


def _span_payload(span: Span) -> dict:
    return {
        "span_id": span.span_id,
        "trace_id": span.trace_id,
        "parent_span_id": span.parent_span_id,
        "name": span.name,
        "span_type": span.span_type,
        "operation": span.operation,
        "status_code": span.status_code,
        "status_message": (span.status_message or "")[:500],
        "service_name": span.service_name,
        "start_time_ns": span.start_time_ns,
        "end_time_ns": span.end_time_ns,
        "duration_ms": round(span.duration_ns / 1_000_000, 1),
        "attributes": safe_json(span.attributes or {}, max_chars=8_000),
        "events": safe_json((span.events or [])[:_MAX_EVENTS]),
    }


def _project_resource(project, uri: str) -> dict:
    return {
        "uri": uri,
        "kind": "project",
        "id": str(project.id),
        "name": project.name,
        "slug": project.slug,
        "integration_type": project.integration_type,
        "is_active": project.is_active,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
    }


def _capability_resource(project, value: str, uri: str) -> dict:
    capability, _ = resolve_capability(project, value)
    if capability is None:
        raise _not_found("capability", value)
    active_model = capability.active_model
    from overbae.services.datasets.rows import capability_dataset_rows

    return {
        "uri": uri,
        "kind": "capability",
        "id": str(capability.id),
        "name": capability.name,
        "slug": capability.slug,
        "description": capability.description[:1_000],
        "status": capability.status,
        "observed": capability.observed,
        "model": capability.model,
        "dataset_size": capability_dataset_rows(capability),
        "active_eval_set": str(capability.active_eval_set_id)
        if capability.active_eval_set_id
        else None,
        "active_model": (
            {
                "id": str(active_model.id),
                "model_id": active_model.model_id,
                "status": active_model.status,
            }
            if active_model is not None
            else None
        ),
        "updated_at": capability.updated_at,
    }


def _trace_resource(project, value: str, uri: str) -> dict:
    queryset = (
        Span.objects.filter(project=project, trace_id=value)
        .select_related("capability", "conversation")
        .order_by("start_time_ns")
    )
    span_count = queryset.count()
    spans = list(queryset[:MAX_INSTRUMENTATION_SPANS])
    if not spans:
        raise _not_found("trace", value)
    root = next((span for span in spans if span.parent_span_id is None), spans[0])
    return {
        "uri": uri,
        "kind": "trace",
        "trace_id": value,
        "span_count": span_count,
        "truncated": span_count > MAX_INSTRUMENTATION_SPANS,
        "root": {
            "span_id": root.span_id,
            "name": root.name,
            "capability": root.capability.slug if root.capability_id else None,
            "status_code": root.status_code,
            "duration_ms": round(root.duration_ns / 1_000_000, 1),
        },
        "spans": [_span_payload(span) for span in spans],
    }


def _session_resource(project, value: str, uri: str) -> dict:
    session, _ = resolve_session(project, value)
    if session is None:
        raise _not_found("session", value)
    spans = list(
        Span.objects.filter(project=project, conversation_id=session.id)
        .order_by("-start_time_ns")
        .values_list("trace_id", flat=True)[: _MAX_LIST * 2]
    )
    trace_ids = list(dict.fromkeys(spans))[:_MAX_LIST]
    return {
        "uri": uri,
        "kind": "session",
        "id": str(session.id),
        "external_id": session.external_id,
        "name": session.name,
        "capability": session.capability.slug if session.capability_id else None,
        "trace_count": len(trace_ids),
        "truncated": len(spans) > len(trace_ids),
        "traces": [
            resource_link("traces", trace_id, f"Trace {trace_id}") for trace_id in trace_ids
        ],
        "created_at": session.created_at,
    }


def _clip_text(value, cap: int) -> str:
    return str(value or "")[:cap]


def _dataset_link(dataset) -> dict[str, str]:
    try:
        from overbae.services.mcp.contracts.datasets import dataset_resource_link
    except ImportError:
        return resource_link("datasets", str(dataset.id), (dataset.name or "Dataset")[:160])
    link = dataset_resource_link(dataset)
    if hasattr(link, "model_dump"):
        return link.model_dump(mode="json", by_alias=True)
    return dict(link)


def _dataset_human_action(dataset) -> dict:
    if dataset.source_kind == Dataset.SourceKind.FILE and dataset.state == Dataset.State.LANDING:
        return {
            "command": "overmind dataset upload FILE --json",
            "arguments": {"file": "<path>", "project_id": str(dataset.project_id)},
        }
    return {
        "command": "overmind dataset export DATASET --json",
        "arguments": {"dataset": str(dataset.id)},
    }


def _dataset_next_actions(dataset) -> list[dict]:
    dataset_id = str(dataset.id)
    if dataset.state in {
        Dataset.State.LANDING,
        Dataset.State.DIAGNOSING,
        Dataset.State.RUNNING,
    }:
        return [
            {
                "tool": "get_job",
                "reason": f"Dataset is {dataset.state}.",
                "arguments": {"kind": "dataset_run", "id": dataset_id},
            }
        ]
    if dataset.state == Dataset.State.ERROR:
        return [
            {
                "tool": "inspect_dataset",
                "reason": "Inspect the dataset error.",
                "arguments": {"dataset": dataset_id},
            },
            {
                "tool": "message_dataset_agent",
                "reason": "Send a corrective dataset-agent message.",
                "arguments": {"dataset": dataset_id},
            },
        ]
    chain = dataset.chain
    proposed = [cell for cell in chain if cell.state == Cell.State.PROPOSED]
    if proposed:
        return [
            {
                "tool": "run_dataset",
                "reason": "User must approve this proposal before it runs.",
                "arguments": {"dataset": dataset_id, "proposal_cell": str(cell.id)},
            }
            for cell in proposed
        ]
    active = dataset.active_cell
    if active is not None:
        ok, reason = active.fits(dataset.intent)
        if not ok:
            return [
                {
                    "tool": "message_dataset_agent",
                    "reason": reason or "Active version failed its contract.",
                    "arguments": {"dataset": dataset_id},
                }
            ]
        arguments = {"dataset": dataset_id, "cell": str(active.id)}
        reason = "Dataset is idle and fitting."
        return [
            {
                "tool": "check_evaluation_readiness",
                "reason": reason,
                "arguments": arguments,
            },
            {
                "tool": "check_finetune_readiness",
                "reason": reason,
                "arguments": arguments,
            },
            {
                "tool": "check_optimizer_readiness",
                "reason": reason,
                "arguments": arguments,
            },
        ]
    return [
        {
            "tool": "message_dataset_agent",
            "reason": "No version has run.",
            "arguments": {"dataset": dataset_id},
        }
    ]


def _active_summary(dataset, versions: dict) -> dict | None:
    active = dataset.active_cell
    if active is None:
        return None
    ok, reason = active.fits(dataset.intent)
    return {
        "id": str(active.id),
        "version": versions.get(active.id, ""),
        "title": active.title,
        "rows": active.rows,
        "fingerprint": active.fingerprint,
        "fits": {"ok": ok, "reason": reason},
    }


def _chat_turn(raw) -> dict | None:
    if not isinstance(raw, dict):
        return None
    cells = raw.get("cells") if isinstance(raw.get("cells"), list) else []
    ms = raw.get("ms")
    return {
        "role": _clip_text(raw.get("role"), 16),
        "text": _clip_text(raw.get("text"), _CHAT_TEXT_CAP),
        "error": _clip_text(raw.get("error"), _ERROR_CAP) or None,
        "cells": safe_json(cells[:20]),
        "at": _clip_text(raw.get("at"), 80) or None,
        "ms": ms if isinstance(ms, int) else None,
    }


def _latest_turn(dataset) -> dict | None:
    chat = dataset.chat or []
    if not chat:
        return None
    return _chat_turn(chat[-1])


def _cell_counts(dataset) -> dict:
    chain = dataset.chain
    counts = Counter(cell.state for cell in chain)
    return {"n": len(chain), "states": dict(counts)}


def _dataset_detail_payload(dataset, *, uri: str, chat_limit: int = _CHAT_DEFAULT) -> dict:
    detail = serialize_dataset_detail(dataset, chat_limit=chat_limit)
    payload = detail.model_dump(mode="json", by_alias=True)
    payload["uri"] = uri
    payload.setdefault("kind", "dataset")
    if not payload.get("human_action"):
        payload["human_action"] = _dataset_human_action(dataset)
    return payload


def dataset_run_job_payload(dataset, uri: str) -> dict:
    versions = dataset.versions()
    active = _active_summary(dataset, versions)
    next_actions = _dataset_next_actions(dataset)
    cells = _cell_counts(dataset)
    dataset_link = _dataset_link(dataset)
    job_link = resource_link(
        "jobs", f"dataset_run/{dataset.id}", (dataset.name or "Dataset run")[:160]
    )
    error = _clip_text(dataset.error, _ERROR_CAP) or None
    return {
        "uri": uri,
        "kind": "dataset_run",
        "id": str(dataset.id),
        "name": dataset.name,
        "status": dataset.state,
        "state": dataset.state,
        "error": error,
        "active": (
            {
                "id": active["id"],
                "version": active["version"],
                "title": active["title"],
                "rows": active["rows"],
            }
            if active
            else None
        ),
        "latest_turn": _latest_turn(dataset),
        "cells": cells,
        "next_action": next_actions[0] if next_actions else None,
        "next_actions": next_actions,
        "dataset": dataset_link,
        "progress": {"cells": cells["states"], "rows": active["rows"] if active else 0},
        "resource_links": [job_link, dataset_link],
        "created_at": dataset.created_at,
        "updated_at": dataset.updated_at,
    }


def _load_dataset_job(project, normalized_id):
    if not normalized_id:
        return None
    return (
        Dataset.objects.filter(project=project, id=normalized_id)
        .select_related("capability", "active")
        .prefetch_related("cells")
        .first()
    )


def _dataset_resource(project, value: str, uri: str) -> dict:
    dataset, _ = resolve_dataset(project, value)
    if dataset is None:
        raise _not_found("dataset", value)
    return _dataset_detail_payload(dataset, uri=uri)


def _eval_run_resource(project, value: str, uri: str) -> dict:
    run, _ = resolve_eval_run(project, value)
    if run is None:
        raise _not_found("eval run", value)
    variants = list(run.variants.order_by("order", "created_at")[:_MAX_LIST])
    return {
        "uri": uri,
        "kind": "eval_run",
        "id": str(run.id),
        "name": run.name,
        "description": run.description[:1_000],
        "status": run.status,
        "data_source": run.data_source,
        "dataset": str(run.dataset_id) if run.dataset_id else None,
        "max_items": run.max_items,
        "sampling": run.sampling,
        "error": run.error[:1_000],
        "summary": safe_json(run.summary or {}),
        "progress": safe_json(compute_run_progress(run)),
        "sample_count": run.samples.count(),
        "variants": [
            {
                "id": str(variant.id),
                "label": variant.label,
                "mode": variant.mode,
                "is_baseline": variant.is_baseline,
            }
            for variant in variants
        ],
        "created_at": run.created_at,
        "completed_at": run.completed_at,
    }


def _finetune_resource(project, value: str, uri: str) -> dict:
    normalized_id = _uuid_ref(value)
    job = (
        FinetuningJob.objects.filter(project=project, id=normalized_id)
        .select_related("capability", "dataset", "deployed_model")
        .first()
        if normalized_id
        else None
    )
    if job is None:
        raise _not_found("finetune", value)
    events = list(job.events.order_by("-created_at")[:_MAX_LIST])
    deployment = getattr(job, "deployed_model", None)
    progress = job.progress if isinstance(job.progress, dict) else {}
    result = job.result if isinstance(job.result, dict) else {}
    metrics = progress.get("metrics") if isinstance(progress.get("metrics"), dict) else {}
    loss = metrics.get("loss") or result.get("epoch_losses") or []
    return {
        "uri": uri,
        "kind": "finetune",
        "id": str(job.id),
        "name": job.name,
        "status": job.status,
        "provider": job.provider,
        "base_model": job.base_model,
        "capability": job.capability.slug if job.capability_id else None,
        "dataset": str(job.dataset_id) if job.dataset_id else None,
        "progress": _safe_finetune_progress(progress),
        "loss": safe_json(loss[:100] if isinstance(loss, list) else []),
        "error": job.error_message[:1_000],
        "deployed_model": str(deployment.id) if deployment is not None else None,
        "events": [
            {
                "id": str(event.id),
                "type": event.event_type,
                "message": event.message[:500],
                "created_at": event.created_at,
            }
            for event in events
        ],
        "created_at": job.created_at,
        "completed_at": job.completed_at,
    }


def _deployment_resource(project, value: str, uri: str) -> dict:
    query = DeployedModel.objects.filter(project=project).select_related("finetuning_job")
    normalized_id = _uuid_ref(value)
    deployment = query.filter(id=normalized_id).first() if normalized_id else None
    if deployment is None:
        deployment = query.filter(model_id=value).first()
    if deployment is None:
        raise _not_found("deployment", value)
    usage = deployment.inference_calls.aggregate(
        request_count=Count("id"),
        prompt_tokens=Sum("prompt_tokens"),
        completion_tokens=Sum("completion_tokens"),
        cost=Sum("cost"),
    )
    prompt_tokens = usage["prompt_tokens"] or 0
    completion_tokens = usage["completion_tokens"] or 0
    return {
        "uri": uri,
        "kind": "deployment",
        "id": str(deployment.id),
        "model_id": deployment.model_id,
        "status": deployment.status,
        "base_model_id": deployment.base_model_id,
        "quantization": deployment.quantization,
        "gpu_type": deployment.gpu_type,
        "is_lora": deployment.is_lora,
        "sla_tier": deployment.sla_tier,
        "inference_url": deployment.inference_url or None,
        "metrics": {
            "request_count": usage["request_count"] or 0,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "cost": float(usage["cost"]) if usage["cost"] is not None else None,
        },
        "finetuning_job": str(deployment.finetuning_job_id)
        if deployment.finetuning_job_id
        else None,
        "error": deployment.error_message[:1_000],
        "created_at": deployment.created_at,
        "deployed_at": deployment.deployed_at,
    }


_DISCONNECTED = {"connected": False, "hostname": "", "cli_version": "", "heartbeat_at": None}


def _optimizer_resource(project, value: str, uri: str) -> dict:
    normalized_id = _uuid_ref(value)
    experiment = (
        OptimizerExperiment.objects.filter(project=project, id=normalized_id)
        .select_related("capability", "dataset")
        .first()
        if normalized_id
        else None
    )
    if experiment is None:
        raise _not_found("optimizer run", value)
    candidates = OptimizerCandidate.objects.select_related("eval_run").order_by("candidate_index")
    iterations = list(
        experiment.iterations.order_by("order").prefetch_related(
            Prefetch("candidates", queryset=candidates)
        )[: _MAX_LIST + 1]
    )
    bounded_iterations = iterations[:_MAX_LIST]
    candidate_rows: list[dict] = []
    iteration_rows = []
    links = [
        resource_link(
            "optimizer-runs", str(experiment.id), f"Optimizer {experiment.capability.name}"
        )
    ]
    for iteration in bounded_iterations:
        iteration_candidates = list(iteration.candidates.all())
        bounded_candidates = iteration_candidates[:_MAX_LIST]
        rows = []
        for candidate in bounded_candidates:
            patch = (candidate.code_path or "")[:_OPTIMIZER_PATCH_CAP]
            row = {
                "id": str(candidate.id),
                "index": candidate.candidate_index,
                "status": candidate.status,
                "is_baseline": candidate.is_baseline,
                "target_model": candidate.target_model or None,
                "score": candidate.score,
                "scores": safe_json(candidate.scores or {}),
                "patch": patch or None,
                "patch_truncated": len(candidate.code_path or "") > _OPTIMIZER_PATCH_CAP,
                "eval_run": (
                    resource_link("eval-runs", str(candidate.eval_run_id), candidate.eval_run.name)
                    if candidate.eval_run_id and candidate.eval_run is not None
                    else None
                ),
            }
            rows.append(row)
            candidate_rows.append(row)
            if row["eval_run"] is not None:
                links.append(row["eval_run"])
        iteration_rows.append(
            {
                "id": str(iteration.id),
                "order": iteration.order,
                "name": iteration.name
                or ("Baseline" if iteration.order == 0 else f"Iteration {iteration.order}"),
                "status": iteration.status,
                "scores": safe_json(iteration.scores or {}),
                "candidates": rows,
                "candidates_truncated": len(iteration_candidates) > len(bounded_candidates),
            }
        )

    state = safe_json(experiment.state or {})
    winner_id = str((experiment.state or {}).get("winner_candidate_id") or "")
    winner = next((row for row in candidate_rows if row["id"] == winner_id), None)
    if winner is None and winner_id:
        selected = (
            OptimizerCandidate.objects.filter(experiment=experiment, id=winner_id)
            .select_related("eval_run")
            .first()
        )
        if selected is not None:
            patch = (selected.code_path or "")[:_OPTIMIZER_PATCH_CAP]
            winner = {
                "id": str(selected.id),
                "target_model": selected.target_model or None,
                "score": selected.score,
                "is_baseline": selected.is_baseline,
                "patch": patch or None,
            }
    comparison = state.get("model_comparison") if isinstance(state, dict) else None
    if winner is None and experiment.mode == OptimizerExperiment.Mode.MODEL_COMPARISON:
        if isinstance(comparison, dict) and comparison.get("overall_winner") == "incumbent":
            winner = {
                "id": None,
                "target_model": None,
                "score": float(comparison.get("incumbent_score") or 0.0),
                "kind": "incumbent",
            }
        elif isinstance(comparison, dict) and comparison.get("selected_winner"):
            selected = (
                OptimizerCandidate.objects.filter(
                    experiment=experiment, target_model=comparison["selected_winner"]
                )
                .order_by("-score", "-iteration__order", "candidate_index")
                .first()
            )
            if selected is not None:
                winner = {
                    "id": str(selected.id),
                    "target_model": selected.target_model or None,
                    "score": selected.score,
                    "kind": "candidate",
                    "is_baseline": selected.is_baseline,
                    "patch": (selected.code_path or "")[:_OPTIMIZER_PATCH_CAP] or None,
                }
    if winner is None:
        possible = [row for row in candidate_rows if not row["is_baseline"]]
        if experiment.mode == OptimizerExperiment.Mode.OPTIMIZE:
            baseline = float((experiment.scores or {}).get("baseline") or 0.0)
            possible = [row for row in possible if row["patch"] and row["score"] > baseline]
        winner = max(possible, key=lambda row: row["score"], default=None)

    terminal = experiment.status in {
        OptimizerExperiment.Status.COMPLETED,
        OptimizerExperiment.Status.FAILED,
        OptimizerExperiment.Status.CANCELLED,
    }
    if terminal:
        next_action = {
            "state": "inspect_result",
            "message": "The optimizer experiment is terminal.",
            "command": None,
        }
    else:
        next_action = {
            "state": "run_executioner",
            "message": "Drive the experiment with the local CLI.",
            "command": f"overmind optimise start -e {experiment.id} && overmind optimise next",
        }
    job_link = resource_link("jobs", f"optimizer_experiment/{experiment.id}", "Optimizer job")
    links.insert(1, job_link)
    return {
        "uri": uri,
        "kind": "optimizer_run",
        "id": str(experiment.id),
        "status": experiment.status,
        "capability": experiment.capability.slug if experiment.capability_id else None,
        "dataset": str(experiment.dataset_id) if experiment.dataset_id else None,
        "mode": experiment.mode,
        "current_iteration": experiment.current_iteration,
        "num_iterations": experiment.num_iterations,
        "scores": safe_json(experiment.scores or {}),
        "state": state,
        "failure_reason": experiment.failure_reason[:1_000],
        "iterations": iteration_rows,
        "iterations_truncated": len(iterations) > len(bounded_iterations),
        "winner": (
            {
                "candidate_id": winner["id"],
                "target_model": winner["target_model"],
                "score": winner["score"],
                "kind": winner.get("kind", "candidate"),
            }
            if winner is not None
            else None
        ),
        # The lease protocol is gone: the local CLI drives the run and never
        # registers a connection, so both blocks are permanently disconnected.
        "executioner": _DISCONNECTED,
        "connection": _DISCONNECTED,
        "next_action": next_action,
        "resource_links": links[: _MAX_LIST + 2],
        "created_at": experiment.created_at,
        "updated_at": experiment.updated_at,
    }


def _job_resource(project, kind: str, value: str, uri: str) -> dict:
    if kind == "eval_run":
        return _eval_run_resource(project, value, uri)
    if kind in {"finetune", "finetune_job"}:
        return _finetune_resource(project, value, uri)
    if kind in {"deployment", "model_deployment"}:
        return _deployment_resource(project, value, uri)
    if kind in {"optimizer", "optimizer_run", "optimizer_experiment"}:
        return _optimizer_resource(project, value, uri)
    normalized_id = _uuid_ref(value)
    if kind == "dataset_run":
        job = _load_dataset_job(project, normalized_id)
        if job is None:
            raise _not_found("dataset run", value)
        return dataset_run_job_payload(job, uri)
    raise _not_found("job", f"{kind}/{value}")


def _connector_resource(project, value: str, uri: str) -> dict:
    connector, _ = resolve_connector(project, value)
    if connector is None:
        raise _not_found("connector", value)
    return connector_resource_payload(project, connector, uri)


def _read_resource_sync(project, raw_uri: str) -> dict:
    parsed = urlparse(raw_uri)
    if parsed.scheme != "overmind":
        raise _not_found("resource", raw_uri)
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    host = parsed.netloc
    if host == "project" and segments == ["current"]:
        return _project_resource(project, raw_uri)
    if host == "dataset-upload" and not segments:
        return _dataset_upload_resource(raw_uri)
    if host == "dataset-export" and not segments:
        return _dataset_export_resource(raw_uri)
    if host == "checkpoint-download" and not segments:
        return _checkpoint_download_resource(raw_uri)
    if host == "connector-setup" and not segments:
        return _connector_setup_resource(raw_uri)
    if (
        host
        in {
            "capabilities",
            "traces",
            "sessions",
            "datasets",
            "eval-runs",
            "finetunes",
            "deployments",
            "optimizer-runs",
            "connectors",
        }
        and len(segments) == 1
    ):
        kind_map = {
            "capabilities": _capability_resource,
            "traces": _trace_resource,
            "sessions": _session_resource,
            "datasets": _dataset_resource,
            "eval-runs": _eval_run_resource,
            "finetunes": _finetune_resource,
            "deployments": _deployment_resource,
            "optimizer-runs": _optimizer_resource,
            "connectors": _connector_resource,
        }
        return kind_map[host](project, segments[0], raw_uri)
    if host == "jobs" and len(segments) == 2:
        return _job_resource(project, segments[0], segments[1], raw_uri)
    raise _not_found("resource", raw_uri)


def resource_list() -> list[types.Resource]:
    return [
        types.Resource(
            name="current-project",
            title="Current project",
            uri="overmind://project/current",
            description="The authenticated project identity and safe metadata.",
            mimeType=JSON_MIME,
        ),
        types.Resource(
            name="dataset-upload",
            title="Local dataset upload",
            uri="overmind://dataset-upload",
            description="CLI guidance for uploading local dataset bytes.",
            mimeType=JSON_MIME,
        ),
        types.Resource(
            name="dataset-export",
            title="Local dataset export",
            uri="overmind://dataset-export",
            description="CLI guidance for downloading committed dataset bytes locally.",
            mimeType=JSON_MIME,
        ),
        types.Resource(
            name="checkpoint-download",
            title="Local checkpoint download",
            uri="overmind://checkpoint-download",
            description="CLI guidance for downloading an archived fine-tuned checkpoint locally.",
            mimeType=JSON_MIME,
        ),
        types.Resource(
            name="connector-setup",
            title="Connector credential setup",
            uri="overmind://connector-setup",
            description="CLI guidance for adding provider credentials without putting them in MCP.",
            mimeType=JSON_MIME,
        ),
    ]


def resource_templates() -> list[types.ResourceTemplate]:
    templates = [
        ("capability", "overmind://capabilities/{capability}", "Capability details"),
        ("trace", "overmind://traces/{trace_id}", "Trace and bounded spans"),
        ("session", "overmind://sessions/{session}", "Session and trace references"),
        ("dataset", "overmind://datasets/{dataset}", "Dataset metadata"),
        ("eval-run", "overmind://eval-runs/{eval_run}", "Evaluation run status"),
        ("finetune", "overmind://finetunes/{job_id}", "Fine-tuning job status"),
        ("deployment", "overmind://deployments/{deployment}", "Deployment status"),
        ("optimizer-run", "overmind://optimizer-runs/{experiment}", "Optimizer run status"),
        ("connector", "overmind://connectors/{connector}", "Connector metadata and sync status"),
        ("job", "overmind://jobs/{kind}/{id}", "Project job status"),
    ]
    return [
        types.ResourceTemplate(
            name=name,
            title=title,
            uriTemplate=template,
            description=title,
            mimeType=JSON_MIME,
        )
        for name, template, title in templates
    ]


async def read_resource(uri: AnyUrl) -> Iterable[ReadResourceContents]:
    context = get_context()
    try:
        payload = await sync_to_async(_read_resource_sync, thread_sensitive=True)(
            context.project, str(uri)
        )
    except MCPError as error:
        code = 404 if error.data.code == "resource_not_found" else 400
        raise McpError(
            types.ErrorData(code=code, message=error.data.message, data=error_payload(error))
        ) from None
    except Exception:
        error = internal_error()
        raise McpError(types.ErrorData(code=500, message=error.data.message)) from None
    return [
        ReadResourceContents(
            content=json.dumps(payload, default=str, ensure_ascii=False), mime_type=JSON_MIME
        )
    ]
