"""Project-scoped MCP resources backed by existing Overmind rows."""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import Counter
from collections.abc import Iterable
from urllib.parse import parse_qs, quote, unquote, urlparse

from asgiref.sync import sync_to_async
from django.conf import settings
from django.db.models import Prefetch
from mcp import types
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.shared.exceptions import McpError
from pydantic import AnyUrl

from modal_shared.decisions import DECISION_OBJECTIVES
from modal_shared.training_telemetry import STARTUP_LABELS
from overbae.api.eval_serializers import compute_run_progress
from overbae.core.errors import InputValidationError
from overbae.models import (
    Capability,
    Cell,
    Dataset,
    DatasetPipeline,
    DatasetPipelineBinding,
    DatasetPipelinePackage,
    DatasetPipelineRun,
    DatasetTransfer,
    DeployedModel,
    EvalSet,
    FinetuningJob,
    InferenceRequest,
    ModelActivation,
    OptimizerCandidate,
    OptimizerExperiment,
    Span,
    TrainingPreparation,
)
from overbae.services import (
    inference_requests,
    model_workflows,
    operational_progress,
    training_monitoring,
)
from overbae.services.datasets import paths, pipeline_bindings, pipeline_packages, workbench
from overbae.services.datasets.imports import status as import_status
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.deployment import deployment_progress
from overbae.services.entity_resolution import (
    resolve_capability,
    resolve_connector,
    resolve_dataset,
    resolve_eval_run,
    resolve_session,
)
from overbae.services.eval.context_check import run_context_checks
from overbae.services.eval.preload_status import read_eval_preload
from overbae.services.eval.sample_io import sample_io
from overbae.services.inference_live import worker_status
from overbae.services.inference_metrics import model_activity, model_metrics, monitoring_options
from overbae.services.mcp.context import get_context, project_context
from overbae.services.mcp.contracts.datasets import (
    next_actions,
    serialize_dataset_detail,
)
from overbae.services.mcp.contracts.instrumentation import MAX_INSTRUMENTATION_SPANS
from overbae.services.mcp.errors import MCPError, error_payload, internal_error
from overbae.services.mcp.references import project_references
from overbae.services.model_activation import activation_progress
from overbae.services.training_preparation import live_progress_for_job
from overbae.services.training_record import run_record

JSON_MIME = "application/json"
_MAX_EVENTS = 20
_MAX_LIST = 50
_OPTIMIZER_PATCH_CAP = 4_000
_ERROR_CAP = 1_000


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
_TOKEN_MEASUREMENTS = frozenset(
    {
        "tokens",
        "supervised_tokens",
        "trained_tokens",
        "max_tokens",
        "max_new_tokens",
        "padded_tokens",
        "padded_token_budget",
        "tokens_per_second",
        "tokens_processed",
        "total_tokens",
        "num_tokens",
        "max_token_length",
        "p95_token_length",
    }
)
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
    from overbae.services.datasets import documents, files

    return {
        "uri": uri,
        "kind": "dataset_upload",
        "extensions": list(files.ALLOWED_SUFFIXES),
        "max_bytes": files.MAX_UPLOAD_BYTES,
        "document_max_bytes": documents.MAX_BYTES,
        "pdf_max_pages": documents.MAX_PAGES,
        "max_files": 100,
        "json_array_max_bytes": files.JSON_ARRAY_MAX_BYTES,
        "limits": (
            f"CSV, TSV, JSON, JSONL, NDJSON, and Parquet uploads are capped at "
            f"{files.MAX_UPLOAD_BYTES // 1024**3} GiB. "
            f"Monolithic JSON arrays are capped at {files.JSON_ARRAY_MAX_BYTES // 1024**2} MiB "
            "because the parser reads them whole."
        ),
        "chunk_bytes": files.CHUNK_BYTES,
        "command": "overmind dataset upload FILE --project-id PROJECT --json",
        "pipeline_packages": {
            "required_for_transformations": True,
            "upload": "overmind dataset pipeline-upload DIRECTORY_OR_ZIP --project-id PROJECT --json",
            "download": "overmind dataset pipeline-download PACKAGE --project-id PROJECT --output pipeline.zip --json",
            "max_bytes": pipeline_packages.MAX_BYTES,
            "max_files": pipeline_packages.MAX_FILES,
            "manifest": {
                "version": 1,
                "runtime": "sha256:<approved immutable image ID>",
                "parameters": {},
                "steps": [
                    {
                        "id": "transform",
                        "input": "source",
                        "name": "Transform",
                        "entrypoint": "transform.py",
                        "input_schema": {"text": "string", "source_row": "integer"},
                        "output_schema": {"text": "string", "source_row": "integer"},
                        "checks": {"preserve_rows": True},
                    }
                ],
            },
            "script_arguments": ["input.jsonl", "output.jsonl", "parameters.json"],
            "inspectability": "Keep each step's meaningful logic in its entrypoint; reserve helpers for reusable functions. Cell transformation metadata binds exact run/revision/package/entrypoint and distinguishes platform execution from external imports. Inspect every retained file via the package resource (file, offset, limit in characters; next_offset until exhausted). Source reads verify the package and file checksums without executing code.",
            "starter_script": "import json, sys\n\nwith open(sys.argv[1], encoding='utf-8') as source, open(sys.argv[2], 'w', encoding='utf-8') as output:\n    for line in source:\n        row = json.loads(line)\n        # Author the requested transformation here; retain source_row.\n        output.write(json.dumps(row, ensure_ascii=False) + '\\n')\n",
            "checks": "preserve_rows and lineage apply in preview and publish. min_rows/max_rows are full-population assertions: preview records them as deferred, publication enforces them. Do not hardcode the first source's size in a reusable recipe unless that size is a real requirement.",
            "lineage": "Retain source_row or declare all contributing _overmind_parent_rows. Preserve duplicate observations and unknown meaning. Use input=source/earlier step or inputs=[distinct earlier IDs] to concatenate disjoint branches in declared order. Overlapping source_row identities fail rather than being deduplicated. Omitted input means the previous step.",
            "flow": "Declare stable step IDs. Script conditions cite expression and entrypoint line; scripts implement selection. Every step executes, including empty outputs. Flow exposes nodes, edges, terminal_steps and unconsumed_steps. Converge training branches into the last step's trainable output, retaining unresolved review metadata. A retained script with inputs consumes combined branches. Cycles, overlapping identities and job-level conditional skipping are unsupported.",
            "runtime": "Inspect inspect_dataset_workbench.runner for approved images and worker freshness. Dependencies are installed in the pinned operator-approved runtime; network and package installation are unavailable during execution. No credentials or host mounts enter script containers.",
            "next": "Register with save_dataset_pipeline, validate, preview and inspect; publish explicitly. Reuse revisions on compatible sources, or save a derived_from variant. Save bindings paused and enable explicitly.",
        },
        "ingestion_verification": "Before upload, inspect local record boundaries, fields, value types and expected row count. For JSON wrappers, explicitly select the intended top-level record array; clarify unresolved ambiguity instead of treating the whole wrapper as one row. Preserve original bytes. After landing, compare source count and representative nested values using inspect_dataset and query_dataset. Resolve mismatches before transformation or consumer handoff. State verification coverage: a sample is not a full-file audit, and successful transfer is not proof of correct ingestion.",
        "readiness": "Run overmind connection check --project-id PROJECT --json from the actual coding environment. It verifies MCP, project access, transfer protocol and read/write access with the same resolved connection. A connected MCP client alone does not establish local transfer readiness.",
        "recovery": "Re-run the identical CLI command to recover its stable transfer key and resume bytes or the existing publication. Use --request-key for an explicitly new upload or a caller-owned identity. Changed content or recipe under the same explicit key conflicts. get_job(kind=dataset_transfer) inspects the receipt without probing or dispatching; dataset_run reports subsequent extraction. Unknown connection errors do not prove sandbox denial. For confirmed network_permission_denied, request the host's supported scoped permission for the same command; do not change sandbox policy, ask for the file again or open a browser.",
        "json_selection": "For a JSON object containing a row array under a nonstandard field, pass --json-rows-field FIELD explicitly. The selected top-level field is recorded in extraction metadata; original file bytes and wrapper metadata remain downloadable. No target meaning is inferred.",
        "written_intent": "Use start_dataset with a brief and the user's explicit intent and capability. Its upload.argv carries exact project/dataset identities; replace FILE with the inspected local path. For a new upload, --brief records the original request.",
        "existing_dataset": "Use overmind dataset upload FILE --project-id PROJECT --dataset DATASET --json to add files to an existing workshop. Each upload appends a recorded import cell after the current chain, preserving earlier versions and source evidence. Inspect the dataset and poll get_job(kind=dataset_run) for completion. REST source accepts uploads; landing does not start preparation.",
        "documents": "PDF, DOCX, Markdown, UTF-8 text and PNG/JPEG/WebP images are extracted by the batch worker (100 MiB per document, 2000 pages per PDF). Upload reservations return the file-type byte limit. Original bytes and element/page evidence are retained. PDF extraction preserves native text and automatically runs local English Tesseract OCR on scanned pages and embedded images. Direct images are capped at 64 megapixels; animated images are rejected. OCR engine/version, page regions, upright image coordinates and recognition confidence are retained. Reading order and visual table structure are not reconstructed. Upload inspection returns rows=null until extraction. get_job(kind=dataset_run) exposes file/stage and measured OCR-page progress under progress.landing.",
        "pdf_text_recovery": {
            "trigger": "Pages where the primary native parser returned no text are checked with PDFium before OCR, including selectable Type 3 font layers.",
            "evidence": "Source extraction.native_text_recovery records engine/version, recovered pages, rows, characters, control_characters and reason. Row extraction methods distinguish pdfium-native-text from tesseract-ocr; page regions remain retained.",
            "limits": "Encoded text is source evidence, not visual verification. Font-encoding artifacts, reading order and diagram relationships require native-agent inspection; the platform does not normalize their meaning. This recovery does not certify partial omissions on pages where the primary parser returned some text.",
        },
        "publication_status": "Without --wait, CLI state/state_scope=at_publication describe the saved publication snapshot, not current extraction. Read get_job(kind=dataset_run) for current progress. --wait returns state_scope=observed_after_landing.",
        "auth": "Account or project API key from --api-key, .overmind/credentials.toml, OVERMIND_API_KEY, or the saved local transfer connection at $XDG_CONFIG_HOME/overmind/connection.toml (default ~/.config/overmind/connection.toml). The saved connection contains an api-key bound to its base-url and must have permissions 0600. MCP authentication alone does not configure CLI authentication. Missing credentials are a local connection error, not a reason to open the Console.",
        "config": (
            "project-id from overmind.toml or --project-id; base URL from OVERMIND_API_URL, "
            "--api-url, or overmind.toml. With no other key, the saved local transfer connection supplies both key and base URL; a different explicitly selected API is rejected."
        ),
        "flow": (
            "POST /api/dataset-transfers/ with project, request_key, filename, size, sha256 and destination recipe; "
            "PUT /api/dataset-transfers/{id}/chunk/?offset= streams bounded bytes; "
            "POST /api/dataset-transfers/{id}/complete/ verifies the hash and publishes once. "
            "Read the receipt or repeat reservation with the identical key to resume. Publication is separate from landing."
        ),
        "multiple_files": (
            "The installed CLI transfers one file per command. Upload the first file, then "
            "attach each remaining file with --dataset DATASET, waiting for dataset_run to "
            "reach idle or error between attachments. Each successful attachment appends an "
            "import cell combining the preceding cell's rows with the new file's rows. Page retained sources "
            "with inspect_dataset. Atomic multi-file landing (up to 100 files) is supported "
            "by REST staging but has no resumable CLI/MCP handoff yet. Managed transfer "
            "upload IDs cannot be republished through /api/uploads/ or source.uploads."
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
        "original_source_command": "overmind dataset export DATASET --source SHA256 --output FILE --json",
        "original_source": "Use a sources[].sha256 from inspect_dataset to retrieve retained original bytes. The CLI verifies SHA-256, refuses redirects and existing output files, and removes a corrupt download. Do not combine --source with --cell or --format csv.",
        "formats": ["jsonl", "csv"],
        "dataset": "Use the dataset id supplied by MCP; the CLI does not resolve dataset names.",
        "auth": "X-Api-Key from --api-key, .overmind/credentials.toml, OVERMIND_API_KEY, or the saved local transfer connection. Missing credentials are a local connection error; do not fall back to the Console.",
        "config": "Base URL from OVERMIND_API_URL, --api-url, or overmind.toml; --path selects overmind.toml. With no other key, use api-key and base-url together from $XDG_CONFIG_HOME/overmind/connection.toml (default ~/.config/overmind/connection.toml, permissions 0600). A different explicitly selected API is rejected.",
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
            measurement = (
                key_text in _TOKEN_MEASUREMENTS
                and type(item) in {int, float}
                and math.isfinite(item)
            )
            if not measurement and _contains_key_part(key_text, _SENSITIVE_COMPACT_PARTS):
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


def safe_finetune_progress(value):
    if isinstance(value, dict):
        result = {}
        for key, item in list(value.items())[:100]:
            key_text = str(key)
            numeric_metric = key_text in {
                "checkpoint_step",
                "restored_step",
                "tokens_per_second",
                "tokens_processed",
                "total_tokens",
                "num_tokens",
            } and isinstance(item, (int, float))
            if not numeric_metric and _contains_key_part(
                key_text, _FINETUNE_PROGRESS_REDACTED_PARTS
            ):
                continue
            result[key_text] = safe_finetune_progress(item)
            if isinstance(item, (list, tuple)) and len(item) > 100:
                result[key_text + "_window"] = {
                    "total": len(item),
                    "returned": 100,
                    "order": "latest",
                    "truncated": True,
                }
        return result
    if isinstance(value, (list, tuple)):
        return [safe_finetune_progress(item) for item in list(value)[-100:]]
    return safe_json(value)


def finetune_progress_payload(value):
    progress = safe_finetune_progress(value)
    if not isinstance(progress, dict):
        return progress
    preparation = progress.get("preparation")
    if isinstance(preparation, dict) and preparation.get("state") in {
        "queued",
        "starting",
        "running",
    }:
        return progress
    diagnostics = progress.get("diagnostics")
    stage = diagnostics.get("stage") if isinstance(diagnostics, dict) else None
    if (stage or progress.get("stage")) in STARTUP_LABELS:
        progress["stage_label"] = STARTUP_LABELS[stage or progress["stage"]]
    if (stage or progress.get("stage")) == "initial_validation":
        progress["stage_label"] = "Pre-training baseline evaluation"
        progress["stage_description"] = (
            "Measuring the starting model on the development set before training begins."
        )
    return progress


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
        "resource_attrs": safe_json(span.resource_attrs or {}, max_chars=8_000),
    }


def _project_resource(project, uri: str) -> dict:
    return {
        "uri": uri,
        "kind": "project",
        "id": str(project.id),
        "name": project.name,
        "slug": project.slug,
        "console_url": f"{settings.FRONTEND_URL.rstrip('/')}/?projectId={project.id}",
        "integration_type": project.integration_type,
        "is_active": project.is_active,
        "created_at": project.created_at,
        "updated_at": project.updated_at,
        "repository_snapshot": (project.settings or {}).get("repository_snapshot"),
        "last_synced_at": (project.settings or {}).get("last_synced_at"),
    }


def _capability_resource(project, value: str, uri: str) -> dict:
    capability, _ = resolve_capability(project, value)
    if capability is None:
        raise _not_found("capability", value)
    capability = Capability.objects.select_related(
        "active_model", "benchmark_model", "activation"
    ).get(pk=capability.pk)
    active_model = capability.active_model
    benchmark = capability.benchmark_model
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
        "eval_preload": read_eval_preload(capability),
        "active_model": (
            {
                "id": str(active_model.id),
                "model_id": active_model.model_id,
                "status": active_model.status,
            }
            if active_model is not None
            else None
        ),
        "activation": activation_progress(getattr(capability, "activation", None)),
        "previous_active_model": str(capability.previous_active_model_id)
        if capability.previous_active_model_id
        else None,
        "first_application_request_at": capability.first_application_request_at,
        "last_application_request_at": capability.last_application_request_at,
        "benchmark_model": {
            "id": str(benchmark.id) if benchmark else None,
            "model_id": benchmark.model_id if benchmark else capability.model,
            "source": "trained" if benchmark else "codebase",
            "status": benchmark.status if benchmark else None,
        },
        "benchmark_candidates": list(
            DeployedModel.objects.filter(
                project=project, finetuning_job__capability=capability, status="ready"
            )
            .order_by("-created_at")
            .values("id", "model_id", "base_model_id")[:100]
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


def _dataset_detail_payload(dataset, *, uri: str) -> dict:
    detail = serialize_dataset_detail(dataset)
    payload = detail.model_dump(mode="json", by_alias=True)
    payload["uri"] = uri
    payload["workbench"] = workbench.describe(dataset)
    payload.setdefault("kind", "dataset")
    if not payload.get("human_action"):
        payload["human_action"] = _dataset_human_action(dataset)
    return payload


def dataset_run_job_payload(dataset, uri: str) -> dict:
    chain = dataset.chain
    versions = dataset.versions(chain=chain)
    ran = [cell for cell in chain if cell.state == Cell.State.OK]
    active = next((cell for cell in ran if cell.id == dataset.active_id), ran[-1] if ran else None)
    actions = [action.model_dump(mode="json") for action in next_actions(dataset, chain, active)]
    cells = {"n": len(chain), "states": dict(Counter(cell.state for cell in chain))}
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
                "id": str(active.id),
                "version": versions.get(active.id, ""),
                "title": active.title,
                "rows": active.rows,
            }
            if active
            else None
        ),
        "cells": cells,
        "next_action": actions[0] if actions else None,
        "next_actions": actions,
        "dataset": dataset_link,
        "progress": {
            "cells": cells["states"],
            "rows": active.rows if active else 0,
            "rows_scope": "active_version",
            "landing": dataset.source_spec.get("landing_progress"),
            "import": import_status(dataset),
        },
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


def _eval_set_resource(project, value: str, uri: str) -> dict:
    eval_set = (
        EvalSet.objects.filter(project=project, id=_uuid_ref(value))
        .select_related("capability")
        .first()
    )
    if eval_set is None:
        raise _not_found("eval set", value)
    members = list(eval_set.members.select_related("evaluator", "evaluator__capability")[:101])
    return {
        "uri": uri,
        "kind": "eval_set",
        "id": str(eval_set.id),
        "name": eval_set.name,
        "capability": str(eval_set.capability_id) if eval_set.capability_id else None,
        "is_active": bool(
            eval_set.capability_id and eval_set.capability.active_eval_set_id == eval_set.id
        ),
        "members": [
            {
                "id": str(member.id),
                "evaluator_id": str(member.evaluator_id),
                "name": member.evaluator.display_name or member.evaluator.name,
                "kind": member.evaluator.kind,
                "role": member.role,
                "enabled": member.enabled,
                "capability": str(member.evaluator.capability_id)
                if member.evaluator.capability_id
                else None,
                "capability_name": member.evaluator.capability.name
                if member.evaluator.capability_id
                else None,
            }
            for member in members[:100]
        ],
        "members_truncated": len(members) > 100,
    }


def _eval_run_resource(project, value: str, uri: str) -> dict:
    run, _ = resolve_eval_run(project, value)
    if run is None:
        raise _not_found("eval run", value)
    variants = list(run.variants.order_by("order", "created_at")[:_MAX_LIST])
    samples = list(
        run.samples.select_related("run__cell", "variant").prefetch_related("scores")[:5]
    )
    sample_count = run.samples.count()
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
        "context_checks": safe_json(run_context_checks(run)),
        "judge_model": run.judge_model,
        "run_evaluators": [
            {
                "id": str(row.id),
                "name": (row.snapshot or {}).get("name", ""),
                "kind": (row.snapshot or {}).get("kind", ""),
                "judge_model": (row.snapshot or {}).get("judge_model", ""),
                "enabled": row.enabled,
            }
            for row in run.run_evaluators.order_by("order", "id")[:_MAX_LIST]
        ],
        "progress": safe_json(compute_run_progress(run)),
        "sample_count": sample_count,
        "samples": [
            {
                "id": str(sample.id),
                "row_index": sample.row_index,
                "variant": sample.variant.label,
                "io": safe_json(sample_io(sample)),
                "error": sample.error[:_ERROR_CAP],
                "scores": safe_json(
                    [
                        {
                            "name": score.name,
                            "value": score.value,
                            "passed": score.passed,
                            "reasoning": score.reasoning,
                        }
                        for score in sample.scores.all()[:50]
                    ]
                ),
            }
            for sample in samples
        ],
        "samples_truncated": sample_count > len(samples),
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
        .select_related(
            "capability",
            "dataset",
            "deployed_model",
            "cell",
            "validation_cell",
            "eval_cell",
            "eval_set",
            "native_evaluation",
        )
        .first()
        if normalized_id
        else None
    )
    if job is None:
        raise _not_found("finetune", value)
    events = list(job.events.order_by("-created_at")[:_MAX_LIST])
    deployment = getattr(job, "deployed_model", None)
    progress = live_progress_for_job(job)
    result = job.result if isinstance(job.result, dict) else {}
    metrics = progress.get("metrics") if isinstance(progress.get("metrics"), dict) else {}
    loss = metrics.get("loss") or result.get("epoch_losses") or []
    return {
        "uri": uri,
        "kind": "finetune",
        "id": str(job.id),
        "group_id": str(job.group_id) if job.group_id else None,
        "name": job.name,
        "status": job.status,
        "provider": job.provider,
        "base_model": job.base_model,
        "record": safe_json(run_record(job)),
        "monitoring": safe_json(training_monitoring.summary(job)),
        "training_objective": (job.hyperparameters or {}).get(
            "objective", "assistant_cross_entropy"
        ),
        "inference_contract": result.get("inference_contract")
        or (
            "decision"
            if (job.hyperparameters or {}).get("objective") in DECISION_OBJECTIVES
            else "chat"
        ),
        "cost_usd": float(job.cost_usd) if job.cost_usd is not None else None,
        "cost_synced_at": job.cost_synced_at,
        "evaluation_plan": {
            "eval_judge_model": job.eval_judge_model,
            "eval_incumbent_before": job.eval_incumbent_before,
            "eval_incumbent_after": job.eval_incumbent_after,
            "eval_model_before": job.eval_model_before,
            "eval_model_after": job.eval_model_after,
        },
        "capability": job.capability.slug if job.capability_id else None,
        "dataset": str(job.dataset_id) if job.dataset_id else None,
        "cell": str(job.cell_id) if job.cell_id else None,
        "eval_cell": str(job.eval_cell_id) if job.eval_cell_id else None,
        "progress": finetune_progress_payload(progress),
        "loss": safe_json(loss[-100:] if isinstance(loss, list) else []),
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
    query = DeployedModel.objects.filter(project=project).select_related(
        "finetuning_job__capability"
    )
    normalized_id = _uuid_ref(value)
    deployment = query.filter(id=normalized_id).first() if normalized_id else None
    if deployment is None:
        deployment = query.filter(model_id=value).first()
    if deployment is None:
        raise _not_found("deployment", value)
    params = parse_qs(urlparse(uri).query, keep_blank_values=True)
    try:
        options = monitoring_options(
            params.get("period", ["all"])[0], params.get("source", ["all"])[0]
        )
    except InputValidationError as exc:
        raise MCPError("invalid_input", exc.detail) from exc
    granularity = {"1h": "minute", "24h": "hour", "7d": "hour", "30d": "day", "all": "day"}[
        options["period"]
    ]
    return {
        "uri": uri,
        "kind": "deployment",
        "id": str(deployment.id),
        "model_id": deployment.model_id,
        "status": deployment.status,
        "progress": deployment_progress(deployment),
        "worker": worker_status(deployment),
        "base_model_id": deployment.base_model_id,
        "quantization": deployment.quantization,
        "gpu_type": deployment.gpu_type,
        "is_lora": deployment.is_lora,
        "sla_tier": deployment.sla_tier,
        "inference_url": deployment.inference_url or None,
        "monitoring": {**options, "granularity": granularity},
        "metrics": model_metrics(deployment, **options),
        "activity": model_activity(deployment, granularity=granularity, **options),
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
    if kind == "inference_request":
        request = InferenceRequest.objects.filter(project=project, pk=_uuid_ref(value)).first()
        if request is None:
            raise _not_found("inference request", value)
        result = inference_requests.describe(request)
        params = parse_qs(urlparse(uri).query)
        try:
            offset, limit = int(params.get("offset", [0])[0]), int(params.get("limit", [32000])[0])
            if offset < 0 or not 1 <= limit <= 32000:
                raise ValueError
        except ValueError:
            raise MCPError(
                "invalid_input", "Use a nonnegative content offset and limit 1–32000"
            ) from None
        content = str(request.result.get("content") or "")
        if request.result:
            result["result"]["content"] = content[offset : offset + limit]
            result["content_page"] = {
                "offset": offset,
                "total": len(content),
                "next_offset": offset + limit if offset + limit < len(content) else None,
            }
        return {"uri": uri, "kind": kind, **result}
    if kind == "model_activation":
        activation = ModelActivation.objects.filter(
            pk=_uuid_ref(value), capability__project=project
        ).first()
        if activation is None:
            raise _not_found("model activation", value)
        return {
            "uri": uri,
            "kind": kind,
            "status": activation.stage,
            **activation_progress(activation),
        }
    if kind in model_workflows.MODELS:
        record = model_workflows.find(project, kind, _uuid_ref(value))
        if record is None:
            raise _not_found(kind, value)
        return {"uri": uri, "kind": kind, **safe_json(model_workflows.describe(kind, record))}
    if kind == "training_preparation":
        prep = TrainingPreparation.objects.filter(
            pk=_uuid_ref(value), cell__dataset__project=project
        ).first()
        if prep is None:
            raise _not_found("training preparation", value)
        return {
            "kind": kind,
            "id": str(prep.id),
            "status": prep.state,
            "report": prep.report,
            "config": prep.config,
            "error": prep.error,
            "resource": resource_link("jobs", f"{kind}/{prep.id}", "Training preparation"),
        }
    if kind == "eval_run":
        return _eval_run_resource(project, value, uri)
    if kind in {"finetune", "finetune_job"}:
        return _finetune_resource(project, value, uri)
    if kind in {"deployment", "model_deployment"}:
        return _deployment_resource(project, value, uri)
    if kind in {"optimizer", "optimizer_run", "optimizer_experiment"}:
        return _optimizer_resource(project, value, uri)
    normalized_id = _uuid_ref(value)
    if kind == "dataset_transfer":
        from overbae.services.datasets import transfers

        transfer = DatasetTransfer.objects.filter(project=project, pk=normalized_id).first()
        if transfer is None:
            raise _not_found("dataset transfer", value)
        return {"uri": uri, "kind": kind, **transfers.describe(transfer)}
    if kind == "dataset_pipeline":
        run = (
            DatasetPipelineRun.objects.filter(dataset__project=project, pk=normalized_id)
            .select_related("dataset", "pipeline__package")
            .first()
            if normalized_id
            else None
        )
        if run is None:
            raise _not_found("dataset pipeline", value)
        return {"uri": uri, "kind": kind, "status": run.state, **workbench.run_record(run)}
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


def interface_resource():
    # Catalog registrars load resources; inspect the catalog only after initialization.
    from overbae.services.mcp.catalog import CATALOG

    context = get_context()
    manifest = [
        tool.model_dump(mode="json")
        for tool in CATALOG.tools(frozenset(context.token.scope.get("permission", [])))
    ]
    parsed = urlparse(context.inference_base_url)
    origin = (
        f"{parsed.scheme}://{parsed.netloc}"
        if parsed.scheme in {"http", "https"} and parsed.netloc
        else None
    )
    return {
        "contract_version": "6.3.0",
        "transfer_protocol_version": 1,
        "catalog_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True).encode()).hexdigest(),
        "tool_count": len(manifest),
        "training_measurements": {
            "monitoring": "A frozen monitoring policy controls bounded development checks, generation scoring, checkpoint selection and optional early stopping. inspect_training_progress pages durable checks and exact evidence without invoking workers. cancel_finetune preserves completed evidence and records remote acknowledgement separately. Development metrics are not final benchmarks. Native target distributions and mean-only denominators remain distinct.",
            "billing": "New Modal training charges use deduplicated worker GPU-time receipts and recorded hardware, never local completion-observation delay. Missing usage remains unmeasured. recorded_basis identifies the retained charge calculation; historical charges without that receipt are not retrospectively relabelled.",
            "forecast": "Matching duration evidence comes from worker usage receipts, excluding collector delay; estimates and recorded charges are not all-in invoices or spend caps.",
            "recovery": "The training collector renews a short lease while alive. Reconciliation closes missed terminal operational events from local receipts without another provider call; worker heartbeat timestamps are preserved.",
        },
        "dataset_transformations": {
            "progress": "Preview and publication expose aggregate seconds plus per-step runtime stage_seconds for container setup, input transfer, script execution, artifact transfer, output reading and cleanup. A stopped script never reports completed runtime; cleanup_confirmed is separate from publication success.",
            "parameters": "Package manifest parameters declare types, for example {seed: integer}; run parameters supply values, for example {seed: 42}.",
            "queries": "Read-only queries have a deployment-configured deadline (default 10 seconds); query_timeout leaves the cell unchanged. Oversized results are rejected during materialization, not clipped.",
            "inspection": "Cell transformation metadata distinguishes recorded script/operation execution, external imports and unrecorded history. It links the exact run, revision, package checksum and entrypoint. Read all retained package files via dataset-pipeline-packages resources using file/offset/limit character pagination; source reads never execute code.",
            "prompt": "author-dataset-transformation",
            "lookup": "inspect_dataset_workbench(pipeline=exact revision ID) returns the recipe and paginated family history; runs and bindings are scoped to that revision",
            "retained_code": "Every new transformation revision requires an uploaded Python package. Author meaningful staged entrypoints, pipeline-upload, save_dataset_pipeline(package=UUID), validate, preview, publish and verify the exact run/cells. Historical package-free revisions remain readable but cannot execute or bind. External imports are attributed results, not retained transformations.",
            "branches": "Acyclic forks and fan-in: input selects one parent; inputs concatenates distinct parents in declared order. Nonempty branch schemas must match; overlapping source_row identities or incompatible schemas fail without coercion. Retained scripts implement routing and consume concatenated branch inputs.",
            "conditions": "Agent-declared expression and retained entrypoint line; never evaluated as code or inferred by the platform",
            "empty_branches": "All steps execute; zero rows are valid unless row checks reject them",
            "output": "Last declared step; all published step cells remain selectable",
            "unsupported": [
                "overlapping row identities across inputs",
                "cycles",
                "job-level conditional skipping",
                "automatic exclusivity or coverage verification",
            ],
            "adaptation": "Reuse the revision for parameters only; new family via derived_from=revision ID for different logic/source contracts; revise a family via pipeline=family ID and expected_revision",
        },
        "connection": {
            "mcp_url": origin + "/api/mcp/" if origin else None,
            "console_url": settings.FRONTEND_URL,
            "identity_basis": "authenticated request endpoint; compare with your intended environment",
        },
        "guidance": {
            "entry": "list_projects; data-only work does not require code scanning or a capability",
            "source": "Pass exact cell UUIDs and fingerprints. Workshop pipelines publish new versions, including from frozen historical parents.",
            "workshop": "The native agent authors and explains. Discover reusable project revisions with inspect_dataset_workbench. Register script packages via CLI and save_dataset_pipeline, validate, preview, then run on pinned source cells. Each step publishes a cell atomically. Adapt with derived_from; original revisions remain unchanged. Explicitly enabled bindings rebuild full source snapshots using the pinned revision. Scripts run only in approved isolated containers, never API/Celery Python. Imports remain externally attributed.",
            "lifecycle": "create saves a draft; prepare verifies inputs; launch is explicit paid authorization",
            "resume": "Use get_job and next_actions. Saved request keys and provider receipts survive reconnects; unresolved submissions are not replayed.",
            "recipes": "Use get_model_catalog for supported model-specific context, batch and training-method constraints. Hyperparameters remain a model-dependent extension; stable partition, sampling, inference and workload settings are typed.",
            "cost": "No implicit spend limit. User constraints bind a reviewed quote; missing cost components stay explicit.",
            "pause": "Pausing a comparison prevents future submissions. It neither cancels in-flight provider work nor configures an external notification monitor.",
        },
        "training_runtime": "Pinned per experiment and job; local catalog identity does not certify deployed GPU code",
        "installed_guidance": "Connected tool schemas are authoritative when installed skills differ",
        "discovery": "Compare this endpoint and catalog hash with the connection you intend to use. Refresh tools/list when the cached inventory differs; do not silently switch environments.",
    }


def _pipeline_content_page(params):
    try:
        offset, limit = int(params.get("offset", [0])[0]), int(params.get("limit", [8000])[0])
        if offset < 0 or not 1 <= limit <= 16000:
            raise ValueError
        return offset, limit
    except ValueError:
        raise MCPError("invalid_input", "Use a nonnegative offset and limit 1–16000.") from None


def _read_resource_sync(project, raw_uri: str) -> dict:
    parsed = urlparse(raw_uri)
    if parsed.scheme != "overmind":
        raise _not_found("resource", raw_uri)
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    host = parsed.netloc
    if host == "interface" and segments == ["current"]:
        return interface_resource()
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
    if host == "dataset-pipelines" and len(segments) == 1:
        recipe = (
            DatasetPipeline.objects.select_related("package")
            .filter(project=project, pk=_uuid_ref(segments[0]))
            .first()
        )
        if recipe is None:
            raise _not_found("pipeline revision", segments[0])
        result = {"uri": raw_uri, **workbench.pipeline_record(recipe)}
        if recipe.package_id:
            result["package_detail"] = pipeline_packages.record(recipe.package)
            result["package_resource"] = resource_link(
                "dataset-pipeline-packages", recipe.package_id, "Retained source package"
            )
        return result
    if host == "dataset-pipeline-bindings" and len(segments) == 1:
        binding = DatasetPipelineBinding.objects.filter(
            project=project, pk=_uuid_ref(segments[0])
        ).first()
        if binding is None:
            raise _not_found("pipeline binding", segments[0])
        return {"uri": raw_uri, **pipeline_bindings.binding_record(binding)}
    if host == "dataset-pipeline-packages" and len(segments) == 1:
        package = DatasetPipelinePackage.objects.filter(
            project=project, pk=_uuid_ref(segments[0])
        ).first()
        if package is None:
            raise _not_found("pipeline package", segments[0])
        result = {"uri": raw_uri, **pipeline_packages.record(package)}
        params = parse_qs(parsed.query)
        filename = params.get("file", [None])[0]
        if filename:
            offset, limit = _pipeline_content_page(params)
            if filename not in {item["path"] for item in package.inventory}:
                raise _not_found("package file", filename)
            try:
                result["file"] = pipeline_packages.source_file(
                    package, filename, offset=offset, limit=limit
                )
            except DatasetError as exc:
                raise MCPError(exc.code, exc.detail) from exc
        return result
    if host == "pipeline-diagnostics" and len(segments) == 1:
        run = DatasetPipelineRun.objects.filter(
            dataset__project=project, pk=_uuid_ref(segments[0])
        ).first()
        if run is None:
            raise _not_found("pipeline run", segments[0])
        params = parse_qs(parsed.query)
        offset, limit = _pipeline_content_page(params)
        try:
            step = int(params.get("step", [0])[0])
            channel = params.get("channel", ["stderr"])[0]
            if not 0 <= step < 20 or channel not in {"stderr", "stdout"}:
                raise ValueError
        except ValueError:
            raise MCPError("invalid_input", "Use step 0–19 and stdout or stderr.") from None
        path = paths.media_root() / "pipeline-runs" / str(run.pk) / f"{step}-{channel}.log"
        content = path.read_text(errors="replace") if path.exists() else ""
        return {
            "run": str(run.pk),
            "step": step,
            "channel": channel,
            "source": "untrusted_script_output",
            "available": path.exists(),
            "retained_limit_bytes": 65536,
            "content": content[offset : offset + limit],
            "total": len(content),
            "next_offset": offset + limit if offset + limit < len(content) else None,
        }
    if host == "operations" and len(segments) == 1:
        params = parse_qs(parsed.query)
        try:
            return operational_progress.inspect(
                project.pk,
                _uuid_ref(segments[0]),
                after=int(params.get("after", [0])[0]),
                limit=int(params.get("limit", [50])[0]),
            )
        except operational_progress.OperationNotFoundError:
            raise _not_found("operation", segments[0]) from None
        except ValueError:
            raise MCPError("invalid_input", "Invalid operational event cursor or limit") from None
    if (
        host
        in {
            "capabilities",
            "traces",
            "sessions",
            "datasets",
            "eval-runs",
            "eval-sets",
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
            "eval-sets": _eval_set_resource,
            "finetunes": _finetune_resource,
            "deployments": _deployment_resource,
            "optimizer-runs": _optimizer_resource,
            "connectors": _connector_resource,
        }
        return kind_map[host](project, segments[0], raw_uri)
    if (
        host == "jobs"
        and len(segments) == 3
        and segments[0] == "data_exploration"
        and segments[2] == "strata"
    ):
        record = model_workflows.find(project, "data_exploration", _uuid_ref(segments[1]))
        if record is None:
            raise _not_found("data_exploration", segments[1])
        params = parse_qs(urlparse(raw_uri).query)
        try:
            return model_workflows.exploration.strata(
                record,
                limit=int(params.get("limit", [100])[0]),
                offset=int(params.get("offset", [0])[0]),
            )
        except ValueError as exc:
            raise MCPError("invalid_input", str(exc)) from None
    if host == "jobs" and len(segments) == 2:
        result = _job_resource(project, segments[0], segments[1], raw_uri)
        operation = operational_progress.latest(project.pk, segments[0], segments[1])
        if operation:
            result["operation"] = operation
            result["operation_resource"] = resource_link(
                "operations", operation["id"], "Operational timeline"
            )
        return result
    raise _not_found("resource", raw_uri)


def resource_list() -> list[types.Resource]:
    return [
        types.Resource(
            name="interface",
            title="Connected interface",
            uri="overmind://interface/current",
            description="Connected contract version, catalog fingerprint and data-first lifecycle rules.",
            mimeType=JSON_MIME,
        ),
        types.Resource(
            name="current-project",
            title="Current project",
            uri="overmind://project/current",
            description="Project identity and safe metadata. Account connections must add ?project_id=ID from list_projects.",
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
        (
            "dataset-pipeline",
            "overmind://dataset-pipelines/{id}",
            "Immutable reusable transformation revision",
        ),
        (
            "dataset-pipeline-package",
            "overmind://dataset-pipeline-packages/{id}",
            "Package inventory; file, offset and limit page retained code",
        ),
        (
            "dataset-pipeline-binding",
            "overmind://dataset-pipeline-bindings/{id}",
            "Source binding, limits and checkpoint",
        ),
        (
            "pipeline-diagnostics",
            "overmind://pipeline-diagnostics/{id}",
            "Untrusted script diagnostics; step, channel, offset and limit",
        ),
        ("eval-run", "overmind://eval-runs/{eval_run}", "Evaluation run status"),
        ("eval-set", "overmind://eval-sets/{eval_set}", "Eval set and evaluator members"),
        ("finetune", "overmind://finetunes/{job_id}", "Fine-tuning job status"),
        ("deployment", "overmind://deployments/{deployment}", "Deployment status"),
        ("optimizer-run", "overmind://optimizer-runs/{experiment}", "Optimizer run status"),
        ("connector", "overmind://connectors/{connector}", "Connector metadata and sync status"),
        ("job", "overmind://jobs/{kind}/{id}", "Project job status"),
        (
            "operation",
            "overmind://operations/{id}",
            "Durable operational events; after and limit page the timeline",
        ),
        (
            "exploration-strata",
            "overmind://jobs/data_exploration/{id}/strata",
            "Sampling strata; optional limit (1–100) and offset query parameters",
        ),
    ]
    return [
        types.ResourceTemplate(
            name=name,
            title=title,
            uriTemplate=template + "{?project_id}",
            description=(
                f"{title}. Current worker state and measurements, inference metrics and activity; optional period=1h|24h|7d|30d|all and source=application|all query parameters (defaults: all)."
                if name == "deployment"
                else title
            ),
            mimeType=JSON_MIME,
        )
        for name, template, title in templates
    ]


async def read_resource(uri: AnyUrl) -> Iterable[ReadResourceContents]:
    context = get_context()
    try:
        if not context.has_permission("read"):
            raise MCPError("permission_denied", "The connection does not grant resource access.")
        parsed = urlparse(str(uri))
        ids = parse_qs(parsed.query, keep_blank_values=True).get("project_id", [])
        if len(ids) > 1:
            raise MCPError("invalid_input", "Pass exactly one project_id.")
        static = parsed.netloc in {
            "interface",
            "dataset-upload",
            "dataset-export",
            "checkpoint-download",
            "connector-setup",
        }
        if not static or ids:
            context = await sync_to_async(project_context, thread_sensitive=True)(
                context, ids[0] if ids else None
            )
        payload = await sync_to_async(_read_resource_sync, thread_sensitive=True)(
            context.project, str(uri)
        )
        if context.project and context.token.scope.get("scope") == "account":
            payload = project_references(payload, context.project.pk)
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
