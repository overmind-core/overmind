"""OTLP trace ingestion.

Post-processing is strictly per-span: spans arrive in any order and batch, so
nothing here may wait for a sibling or the trace root.
"""

from __future__ import annotations

import logging
import zlib
from datetime import UTC, datetime
from typing import Any

from django.db import transaction
from django.http import HttpResponseBase, JsonResponse
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated

from overbae.api import overmind_attrs as oc_attrs
from overbae.api.scoping import project_ids_for
from overbae.core.utils import safe_float, safe_int, safe_json, safe_json_or_default
from overbae.models import (
    APIToken,
    Capability,
    Conversation,
    Project,
    ProjectMembership,
    Span,
)
from overbae.models.traces import usage_slice
from overbae.services.behaviour.binder import attach_inferred_vcs_sha
from overbae.services.capabilities import identity

logger = logging.getLogger(__name__)


def _get_attr(mapping: dict[str, Any] | None, primary: str, *legacy: str) -> Any:
    """First non-empty value across *primary* then the *legacy* keys."""
    if not mapping:
        return None
    for key in (primary, *legacy):
        val = mapping.get(key)
        if val not in (None, "", [], {}):
            return val
    return None


# OTel marks "no parent" with eight zero bytes.
_EMPTY_PARENT_SPAN_ID = b"\x00" * 8


# Recognises optimize spans that carry no ``overmind.command``; only the root
# CLI span has that tag.


def _any_value_to_python(value) -> Any:
    kind = value.WhichOneof("value")
    match kind:
        case "string_value":
            return value.string_value
        case "int_value":
            return value.int_value
        case "double_value":
            return value.double_value
        case "bool_value":
            return value.bool_value
        case "array_value":
            return [_any_value_to_python(item) for item in value.array_value.values]
        case "kvlist_value":
            return {kv.key: _any_value_to_python(kv.value) for kv in value.kvlist_value.values}
        case "bytes_value":
            return value.bytes_value.hex()
        case _:
            return None


def _kv_list_to_dict(attributes) -> dict[str, Any]:
    return {attr.key: _any_value_to_python(attr.value) for attr in attributes}


# canonical key → alias priority order. Applied via setdefault so native keys
# win; lets OTel GenAI semconv and OpenInference spans land with usable evidence.
_GENAI_COALESCE: dict[str, tuple[str, ...]] = {
    oc_attrs.LLM_MODEL: ("gen_ai.request.model", "gen_ai.model", "llm.model_name"),
    oc_attrs.LLM_RESPONSE_MODEL: ("gen_ai.response.model",),
    oc_attrs.LLM_PROVIDER: ("gen_ai.provider.name", "gen_ai.system", "llm.provider", "llm.system"),
    oc_attrs.LLM_USAGE_PROMPT_TOKENS: (
        "gen_ai.usage.input_tokens",
        "gen_ai.usage.prompt_tokens",
        "llm.token_count.prompt",
    ),
    oc_attrs.LLM_USAGE_COMPLETION_TOKENS: (
        "gen_ai.usage.output_tokens",
        "gen_ai.usage.completion_tokens",
        "llm.token_count.completion",
    ),
    oc_attrs.LLM_USAGE_TOTAL_TOKENS: ("gen_ai.usage.total_tokens", "llm.token_count.total"),
    oc_attrs.LLM_CACHE_READ_TOKENS: (
        "gen_ai.usage.cache_read_input_tokens",
        "gen_ai.usage.cache_read_tokens",
        "llm.token_count.prompt_details.cache_read",
    ),
    oc_attrs.TOOL_NAME: ("gen_ai.tool.name",),
    oc_attrs.INPUT_DATA: ("input.value",),
    oc_attrs.OUTPUT_DATA: ("output.value",),
}


def _coalesce_genai_attrs(attrs: dict[str, Any]) -> dict[str, Any]:
    for canonical, aliases in _GENAI_COALESCE.items():
        for alias in aliases:
            value = attrs.get(alias)
            if value is not None and value != "":
                attrs.setdefault(canonical, value)
                break
    return attrs


def _span_event_to_dict(event) -> dict[str, Any]:
    return {
        "time_unix_nano": event.time_unix_nano,
        "name": event.name,
        "attributes": _kv_list_to_dict(event.attributes),
    }


def _span_link_to_dict(link) -> dict[str, Any]:
    return {
        "trace_id": link.trace_id.hex(),
        "span_id": link.span_id.hex(),
        "attributes": _kv_list_to_dict(link.attributes),
    }


def _parent_hex_or_none(span_proto) -> str | None:
    parent = span_proto.parent_span_id
    if not parent or parent == _EMPTY_PARENT_SPAN_ID:
        return None
    return parent.hex()


def _normalize_scope_name(name: str) -> str:
    """Legacy Traceloop scopes are renamed into the product namespace."""
    return (name or "").replace("@traceloop/", "@overmind/")


# Last-resort hints for _classify_span_type, when neither an explicit tag nor a
# GenAI ``execute_tool`` marker is present.
_TOOL_NAME_HINTS: tuple[str, ...] = (
    "tool",
    "function_call",
    "function.call",
    "execute_tool",
)


# Third-party instrumentors (openinference: langchain, llama-index, ...) stamp
# their own kind taxonomy; without this map every non-tool span of theirs fell
# through to the llm_call default.
_OPENINFERENCE_SPAN_TYPES: dict[str, str] = {
    "llm": "llm_call",
    "embedding": "llm_call",
    "tool": "tool_call",
    "retriever": "retrieval",
    "reranker": "retrieval",
    "chain": "workflow",
    "agent": "workflow",
    "guardrail": "workflow",
    "evaluator": "workflow",
}


def _classify_span_type(span_name: str, attrs: dict[str, Any]) -> str:
    """Heuristic ``Span.SpanType`` from the span's name and attributes.

    An explicit tag wins and passes through verbatim, so a future SDK enum
    lands in the column with no server change. Everything unmatched defaults to
    ``llm_call``, which is why ``span_type=llm_call`` is not evidence of one.
    """
    explicit = attrs.get(oc_attrs.SPAN_TYPE) or attrs.get("overmind.span_type") or attrs.get("type")
    if isinstance(explicit, str) and explicit:
        return explicit

    oi_kind = str(attrs.get("openinference.span.kind") or "").strip().lower()
    if oi_kind in _OPENINFERENCE_SPAN_TYPES:
        return _OPENINFERENCE_SPAN_TYPES[oi_kind]

    op = (attrs.get("gen_ai.operation.name") or "").strip().lower()
    if op == "execute_tool" or attrs.get(oc_attrs.TOOL_NAME):
        return Span.SpanType.TOOL_CALL

    name = (span_name or "").lower()
    if any(hint in name for hint in _TOOL_NAME_HINTS):
        return Span.SpanType.TOOL_CALL

    return Span.SpanType.LLM_CALL


def _operation_from(span_proto, attrs: dict[str, Any]) -> str:
    """Trimmed to the column width."""
    op = (
        attrs.get("gen_ai.operation.name")
        or attrs.get("operation")
        or attrs.get(oc_attrs.TOOL_NAME)
    )
    return str(op or span_proto.name or "")[:512]


def resolve_project_for_ingest(request, resource_attrs: dict[str, Any]) -> Project | None:
    """Project-scoped keys pin ingest; account keys and sessions resolve from attrs or membership."""
    if isinstance(request.auth, APIToken) and not request.auth.is_account_scoped:
        return request.auth.project

    explicit_id = _get_attr(resource_attrs, oc_attrs.PROJECT_ID, "overmind.project_id")
    if explicit_id:
        try:
            project = Project.objects.get(id=explicit_id)
        except Project.DoesNotExist:
            project = None
        else:
            if ProjectMembership.objects.filter(user=request.user, project=project).exists():
                return project

    membership = (
        ProjectMembership.objects.filter(user=request.user)
        .select_related("project")
        .order_by("created_at")
        .first()
    )
    return membership.project if membership else None


def _normalize_product_attr_key(key: Any) -> str | None:
    """Canonical ``overmind.*`` key, or ``None`` when *key* is not a product tag.
    ``overclaw.*`` is the old prefix and still arrives from pinned SDKs."""
    if not isinstance(key, str):
        return None
    if key.startswith("overmind."):
        return key
    if key.startswith("overclaw."):
        return "overmind." + key.removeprefix("overclaw.")
    return None


# Capabilities sometimes embed ``overmind.*`` metadata inside these payloads (dict or
# JSON string) instead of stamping top-level span attributes, so they are scanned.
_PAYLOAD_ATTR_KEYS: tuple[str, ...] = (
    oc_attrs.INPUT_DATA,
    oc_attrs.OUTPUT_DATA,
    "overmind.input_data",
    "overmind.output_data",
    "inputs",
    "outputs",
    "traceloop.entity.input",
    "traceloop.entity.output",
)

# Guards against hostile or self-referential JSON; real payloads are 2–3 deep.
_MAX_PAYLOAD_DEPTH = 6


def _maybe_parse_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if not stripped or stripped[0] not in '{["':
        return value
    parsed = safe_json(value)
    return parsed if parsed is not None else value


def _collect_overmind_tags(value: Any, out: dict[str, Any], depth: int = 0) -> None:
    """Harvest ``overmind.*`` keys out of a dict, list or JSON string. Anything
    unrecognisable is ignored — this walks untrusted payloads."""
    if depth > _MAX_PAYLOAD_DEPTH or value is None:
        return

    if isinstance(value, str):
        parsed = _maybe_parse_json(value)
        if parsed is not value:
            _collect_overmind_tags(parsed, out, depth + 1)
        return

    if isinstance(value, dict):
        for key, child in value.items():
            norm = _normalize_product_attr_key(key)
            if norm is not None:
                # Existing keys (added at higher priority) are preserved.
                out.setdefault(norm, child)
            _collect_overmind_tags(child, out, depth + 1)
        return

    if isinstance(value, (list, tuple)):
        for item in value:
            _collect_overmind_tags(item, out, depth + 1)


def _overmind_tags_from_span(span: Span) -> dict[str, Any]:
    """Every ``overmind.*`` tag visible on *span*.

    Priority runs lowest to highest: nested payloads, then resource attributes,
    then span attributes. The order of the three blocks below is the contract.
    """
    tags: dict[str, Any] = {}
    attrs = span.attributes or {}

    for key in _PAYLOAD_ATTR_KEYS:
        if key in attrs:
            _collect_overmind_tags(attrs[key], tags)

    for key, val in (span.resource_attrs or {}).items():
        norm = _normalize_product_attr_key(key)
        if norm is not None:
            tags[norm] = val

    for key, val in attrs.items():
        norm = _normalize_product_attr_key(key)
        if norm is not None:
            tags[norm] = val

    return tags


def _first(tags: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        val = tags.get(key)
        if val:
            return val
    return None


def _find_existing_capability_for_trace(trace_id: str, project: Project) -> Capability | None:
    """Subprocess-spawned spans inherit W3C ``TRACEPARENT`` but not OTel baggage, so
    they carry no capability identity. Reuse is only safe when the trace maps to
    exactly one capability — on a handoff trace an arbitrary pick would smear one
    capability's identity across the boundary."""
    if not trace_id:
        return None
    capability_ids = list(
        Span.objects.filter(trace_id=trace_id, capability__isnull=False, project=project)
        .values_list("capability_id", flat=True)
        .distinct()[:2]
    )
    if len(capability_ids) != 1:
        return None
    return Capability.objects.filter(pk=capability_ids[0], project=project).first()


def _resolve_capability(
    project: Project,
    resource_attrs: dict[str, Any],
    overmind_tags: dict[str, Any],
) -> Capability | None:
    """Attribute a span to a current capability, or to the graph floor.

    ``overmind.capability.id`` is the only mapping key; ``overmind.capability.name``
    is an accessibility label and never resolves. Span-level identity wins over
    resource identity: resource attributes are process-global (the FIRST
    ``overmind.init()`` pins them), so a multi-capability process stamping
    per-request identity must not be overridden.
    Ingest never creates a capability — an id the project does not have
    returns ``None`` and the span stays unbound, visibly."""
    resource_id = resource_attrs.get(oc_attrs.CAPABILITY_ID)
    tagged_id = overmind_tags.get(oc_attrs.CAPABILITY_ID)
    # Tags merge resource then span — a resource-only value is
    # indistinguishable from a span stamp unless it differs from the resource.
    span_level_id = tagged_id if tagged_id and tagged_id != resource_id else None
    for probe in (span_level_id, resource_id):
        if not probe:
            continue
        resolved = identity.lookup(project.id, str(probe))
        if resolved is not None:
            return resolved
    if tagged_id or resource_id:
        logger.info(
            "Unresolved capability id %r for project %s — span stays unbound",
            str(tagged_id or resource_id)[:64],
            project.slug,
        )
    return None


def _resolve_conversation(
    project: Project, span: Span, capability: Capability | None
) -> Conversation | None:
    """Sessions are project-wide; the ``capability`` FK is only a convenience pointer
    to the first capability seen."""
    external_id = _get_attr(
        span.attributes, oc_attrs.CONVERSATION_ID, oc_attrs.CONVERSATION_ID_LEGACY
    ) or _get_attr(span.resource_attrs, oc_attrs.CONVERSATION_ID, oc_attrs.CONVERSATION_ID_LEGACY)
    if not external_id or not isinstance(external_id, str):
        return None

    conversation, created = Conversation.objects.get_or_create(
        project=project, external_id=external_id[:512]
    )
    if created:
        logger.info("Auto-created session %s/%s", project.slug, external_id[:64])
    if capability is not None and conversation.capability_id is None:
        conversation.capability = capability
        conversation.save(update_fields=["capability"])
    return conversation


def _assign(capability: Capability, field: str, value: Any, *, force: bool) -> bool:
    """Sets *field* only when empty, unless *force*."""
    if value is None or value == "":
        return False
    cur = getattr(capability, field)
    if (force or not cur) and cur != value:
        setattr(capability, field, value)
        return True
    return False


# ``cast`` applies to non-empty values only — an empty container must never
# overwrite populated state.
_EVAL_FIELD_TYPES: dict[str, tuple[type, Any]] = {
    "input_schema": (dict, {}),
    "output_fields": (dict, {}),
    "structure_weight": (float, 20.0),
    "total_points": (float, 100.0),
    "tool_config": (dict, {}),
    "tool_usage_weight": (float, 10.0),
    "consistency_rules": (list, []),
    "optimizable_elements": (list, []),
    "fixed_elements": (list, []),
}


def _merge_eval_spec(capability: Capability, spec: dict[str, Any], *, force: bool) -> bool:
    """Apply an ``eval_spec.json``-shaped dict onto Capability columns."""
    dirty = False

    for field, (cast, _default) in _EVAL_FIELD_TYPES.items():
        new_val = spec.get(field)
        if new_val is None:
            continue
        current = getattr(capability, field)

        # Never wipe populated structured state with an empty container.
        if cast in (dict, list) and current and not new_val:
            continue
        # Without ``force`` we only fill empty fields.
        if not force and cast in (dict, list) and current:
            continue

        if cast is float:
            new_val = safe_float(new_val, _default)
        setattr(capability, field, new_val)
        dirty = True

    # The SDK's eval_spec.json key is frozen wire format: ``agent_description``.
    desc = spec.get("agent_description") or spec.get("description")
    if isinstance(desc, dict):
        desc = desc.get("purpose") or desc.get("description") or ""
    if isinstance(desc, str) and desc.strip():
        dirty |= _assign(capability, "description", desc.strip(), force=force)

    out_schema = spec.get("output_schema")
    if isinstance(out_schema, dict) and out_schema and (force or not capability.output_schema):
        capability.output_schema = out_schema
        dirty = True

    return dirty


def _apply_overmind_fields(capability: Capability, overmind_tags: dict[str, Any]) -> bool:
    command = overmind_tags.get(oc_attrs.COMMAND)
    # setup/optimize spans carry authoritative spec data — overwrite freely.
    force_eval = command in ("optimize", "setup")
    force_path = force_eval

    dirty = False

    fn_name = _first(overmind_tags, oc_attrs.SETUP_ENTRYPOINT_FN, oc_attrs.OPTIMIZE_ENTRYPOINT_FN)

    if fn_name:
        dirty |= _assign(capability, "entrypoint_fn", fn_name, force=force_path)

    # Always overwritten: the latest span wins, so the UI can flag a capability whose
    # most recent run used an outdated CLI.
    cli_version = overmind_tags.get(oc_attrs.CLI_VERSION)
    if cli_version and isinstance(cli_version, str):
        dirty |= _assign(capability, "cli_version", cli_version[:20], force=True)

    dirty |= _assign(capability, "model", overmind_tags.get(oc_attrs.SETUP_MODEL), force=force_eval)
    dirty |= _assign(
        capability,
        "analyzer_model",
        _first(overmind_tags, oc_attrs.SETUP_ANALYZER_MODEL, oc_attrs.OPTIMIZE_ANALYZER_MODEL),
        force=force_eval,
    )

    eval_raw = _first(overmind_tags, oc_attrs.OPTIMIZE_EVAL_SPEC, oc_attrs.SETUP_EVAL_SPEC)
    if eval_raw:
        spec = safe_json_or_default(eval_raw, {})
        if spec:
            dirty |= _merge_eval_spec(capability, spec, force=force_eval)

    policy_md = _first(overmind_tags, oc_attrs.SETUP_POLICY_MARKDOWN)
    if policy_md:
        dirty |= _assign(capability, "policy_markdown", policy_md, force=force_eval)

    out_schema = safe_json_or_default(overmind_tags.get(oc_attrs.SETUP_OUTPUT_SCHEMA), None)
    if out_schema and (force_eval or out_schema != capability.output_schema):
        capability.output_schema = out_schema
        dirty = True

    in_schema = safe_json_or_default(overmind_tags.get(oc_attrs.SETUP_INPUT_SCHEMA), None)
    if isinstance(in_schema, dict) and in_schema and (force_eval or not capability.input_schema):
        capability.input_schema = in_schema
        dirty = True

    consistency = safe_json_or_default(overmind_tags.get(oc_attrs.SETUP_CONSISTENCY_RULES), None)
    if (
        isinstance(consistency, list)
        and consistency
        and (force_eval or not capability.consistency_rules)
    ):
        capability.consistency_rules = consistency
        dirty = True

    optimizable = safe_json_or_default(overmind_tags.get(oc_attrs.SETUP_OPTIMIZABLE_ELEMENTS), None)
    if (
        isinstance(optimizable, list)
        and optimizable
        and (force_eval or not capability.optimizable_elements)
    ):
        capability.optimizable_elements = optimizable
        dirty = True

    fixed = safe_json_or_default(overmind_tags.get(oc_attrs.SETUP_FIXED_ELEMENTS), None)
    if isinstance(fixed, list) and fixed and (force_eval or not capability.fixed_elements):
        capability.fixed_elements = fixed
        dirty = True

    tools_summary = overmind_tags.get(oc_attrs.SETUP_TOOLS_SUMMARY)
    if isinstance(tools_summary, str) and tools_summary.strip():
        dirty |= _assign(capability, "tools_summary", tools_summary, force=force_eval)

    decision_logic = overmind_tags.get(oc_attrs.SETUP_DECISION_LOGIC)
    if isinstance(decision_logic, str) and decision_logic.strip():
        dirty |= _assign(capability, "decision_logic", decision_logic, force=force_eval)

    return dirty


# Priority order, mirroring the SDK's ``overmind/genai_usage.py``, so ingest also
# reads native OTel GenAI semconv and Traceloop spans.
_PROMPT_TOKEN_KEYS = (
    oc_attrs.LLM_USAGE_PROMPT_TOKENS,
    oc_attrs.LLM_PROMPT_TOKENS,
    "gen_ai.usage.prompt_tokens",
    "gen_ai.usage.input_tokens",
    "llm.usage.prompt_tokens",
)
_COMPLETION_TOKEN_KEYS = (
    oc_attrs.LLM_USAGE_COMPLETION_TOKENS,
    oc_attrs.LLM_COMPLETION_TOKENS,
    "gen_ai.usage.completion_tokens",
    "gen_ai.usage.output_tokens",
    "llm.usage.completion_tokens",
)
_TOTAL_TOKEN_KEYS = (
    oc_attrs.LLM_USAGE_TOTAL_TOKENS,
    oc_attrs.LLM_TOTAL_TOKENS,
    "gen_ai.usage.total_tokens",
    "llm.usage.total_tokens",
)
_CACHE_READ_TOKEN_KEYS = (
    oc_attrs.LLM_CACHE_READ_TOKENS,
    "gen_ai.usage.cache_read_input_tokens",
    "gen_ai.usage.cache_read_tokens",
)
_MODEL_KEYS = (
    oc_attrs.LLM_MODEL,
    oc_attrs.LLM_RESPONSE_MODEL,
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.model",
)


def _first_int(attrs: dict[str, Any], keys: tuple[str, ...]) -> int:
    for key in keys:
        value = safe_int(attrs.get(key))
        if value:
            return value
    return 0


def _compute_cost(
    model: str | None, prompt_tokens: int, completion_tokens: int, cache_read_tokens: int = 0
) -> float:
    """``0.0`` when there is no model, no tokens, or no pricing entry — cost
    derivation must never break ingest."""
    from genai_prices import Usage, calc_price

    if not model or not (prompt_tokens or completion_tokens):
        return 0.0
    try:
        price = calc_price(
            Usage(
                input_tokens=prompt_tokens or None,
                output_tokens=completion_tokens or None,
                cache_read_tokens=cache_read_tokens or None,
            ),
            model_ref=model,
        )
    except LookupError:
        logger.debug("genai_prices has no pricing for model %r", model)
        return 0.0
    # genai_prices returns Decimal; usage_stats math stays float-only.
    return safe_float(price.total_price) if price.total_price else 0.0


def _build_span_usage(span: Span) -> dict[str, Any]:
    attrs = span.attributes or {}
    span_type = (span.span_type or "").lower()

    pt = _first_int(attrs, _PROMPT_TOKEN_KEYS)
    ct = _first_int(attrs, _COMPLETION_TOKEN_KEYS)
    tt = _first_int(attrs, _TOTAL_TOKEN_KEYS)
    cache_read = _first_int(attrs, _CACHE_READ_TOKEN_KEYS)

    model = _get_attr(attrs, *_MODEL_KEYS)

    # Trust a client-reported cost; otherwise derive it from token usage.
    cost = safe_float(attrs.get(oc_attrs.LLM_COST)) or _compute_cost(model, pt, ct, cache_read)

    llm_calls = 1 if span_type == Span.SpanType.LLM_CALL else 0
    tool_calls = 1 if span_type in (Span.SpanType.TOOL_CALL, "tool") else 0

    models = {model: 1} if model else {}

    if not any((pt, ct, tt, cost, llm_calls, tool_calls, models)):
        return {}

    return {
        "prompt_tokens": pt,
        "completion_tokens": ct,
        "total_tokens": tt or (pt + ct),
        "cost_usd": round(safe_float(cost), 6) if cost else 0.0,
        "llm_calls": llm_calls,
        "tool_calls": tool_calls,
        "models": models,
    }


def _apply_runtime_summaries(
    capability: Capability, span: Span, overmind_tags: dict[str, Any]
) -> bool:
    """Cumulative counters accumulate across spans, deduped by ``span_id`` so a
    re-ingest of the same batch stays idempotent."""
    dirty = False

    span_usage = _build_span_usage(span)
    if span_usage:
        existing = dict(capability.usage_stats or {})
        seen: list[str] = list(existing.get("_spans") or [])
        if span.span_id not in seen:
            for key in (
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
                "llm_calls",
                "tool_calls",
            ):
                existing[key] = (existing.get(key) or 0) + span_usage.get(key, 0)
            if span_usage.get("cost_usd"):
                existing["cost_usd"] = round(
                    safe_float(existing.get("cost_usd")) + safe_float(span_usage["cost_usd"]),
                    6,
                )
            merged_models = dict(existing.get("models") or {})
            for model, n in (span_usage.get("models") or {}).items():
                merged_models[model] = merged_models.get(model, 0) + n
            if merged_models:
                existing["models"] = merged_models

            seen.append(span.span_id)
            # Capped so the JSON blob stays bounded.
            existing["_spans"] = seen[-2000:]
            existing["updated_at"] = timezone.now().isoformat()
            capability.usage_stats = existing
            dirty = True

    if span.end_time_ns:
        end_dt = datetime.fromtimestamp(span.end_time_ns / 1_000_000_000, tz=UTC)
        if capability.last_activity_at is None or end_dt > capability.last_activity_at:
            capability.last_activity_at = end_dt
            dirty = True

    return dirty


def process_span(span_id: str, project_id: str | None = None):
    """Project a single span onto its Capability row.

    Idempotent, because the task is retried and the same batch is re-exported:
    Capability fields are last-write-wins and the usage counters dedupe by ``span_id``.
    """
    qs = Span.objects.filter(pk=span_id).select_related("project")
    if project_id:
        qs = qs.filter(project_id=project_id)
    span: Span = qs.first()
    if span is None:
        logger.warning("process_span: span %s not found, skipping", span_id)
        return

    project = span.project
    resource_attrs = dict(span.resource_attrs or {})
    overmind_tags = _overmind_tags_from_span(span)

    with transaction.atomic():
        capability = _resolve_capability(project, resource_attrs, overmind_tags)
        if capability is None:
            capability = _find_existing_capability_for_trace(span.trace_id, project)
        if capability is not None:
            overmind_tags[oc_attrs.CLI_VERSION] = span.scope_version
            dirty = _apply_overmind_fields(capability, overmind_tags)
            dirty |= _apply_runtime_summaries(capability, span, overmind_tags)
            if dirty:
                capability.save()

        conversation = _resolve_conversation(project, span, capability)

    fields_to_persist: list[str] = []
    if capability and span.capability_id != capability.pk:
        span.capability = capability
        fields_to_persist.append("capability")
    if conversation and span.conversation_id != conversation.pk:
        span.conversation = conversation
        fields_to_persist.append("conversation")
    if fields_to_persist:
        span.save(update_fields=fields_to_persist)

    logger.info(
        "Processed span %s trace=%s → capability=%s",
        span_id,
        span.trace_id,
        capability.slug if capability else None,
    )


def _build_span(
    span_proto,
    project: Project,
    resource_attrs: dict[str, Any],
    scope_name: str,
    scope_version: str,
) -> Span:
    start = span_proto.start_time_unix_nano
    end = span_proto.end_time_unix_nano
    duration = end - start if end and start else 0
    attrs = _coalesce_genai_attrs(_kv_list_to_dict(span_proto.attributes))

    return Span(
        span_id=span_proto.span_id.hex(),
        trace_id=span_proto.trace_id.hex(),
        parent_span_id=_parent_hex_or_none(span_proto),
        project=project,
        span_type=_classify_span_type(span_proto.name, attrs),
        operation=_operation_from(span_proto, attrs),
        name=span_proto.name or "",
        kind=int(span_proto.kind),
        start_time_ns=start,
        end_time_ns=end,
        duration_ns=duration,
        status_code=int(span_proto.status.code),
        status_message=span_proto.status.message or "",
        service_name=str(resource_attrs.get("service.name") or ""),
        resource_attrs=resource_attrs,
        scope_name=_normalize_scope_name(scope_name),
        scope_version=scope_version or "",
        attributes=attrs,
        usage=usage_slice(attrs),
        events=[_span_event_to_dict(e) for e in span_proto.events],
        links=[_span_link_to_dict(link) for link in span_proto.links],
    )


def _build_spans_for_resource(
    request,
    resource_span: ResourceSpans,
) -> tuple[list[Span], Project | None] | JsonResponse:
    resource_attrs = _kv_list_to_dict(resource_span.resource.attributes)
    project = resolve_project_for_ingest(request, resource_attrs)
    if project is None:
        logger.warning("No project for user %s, skipping resource_span", request.user)
        return [], None

    allowed_ids = {str(pid) for pid in project_ids_for(request.user, request.auth)}
    if str(project.id) not in allowed_ids:
        return JsonResponse({"error": "Project access denied."}, status=403)

    resource_attrs = attach_inferred_vcs_sha(str(project.id), resource_attrs)

    spans: list[Span] = []
    for scope_span in resource_span.scope_spans:
        scope_name = scope_span.scope.name
        scope_version = scope_span.scope.version
        for span_proto in scope_span.spans:
            spans.append(
                _build_span(span_proto, project, resource_attrs, scope_name, scope_version)
            )
    return spans, project


# Updated when the same ``span_id`` is re-ingested. The experiment FKs
# (capability / job / iteration) are owned by ``process_span`` and must stay out.
_SPAN_UPSERT_FIELDS = [
    "name",
    "kind",
    "span_type",
    "operation",
    "parent_span_id",
    "start_time_ns",
    "end_time_ns",
    "duration_ns",
    "status_code",
    "status_message",
    "service_name",
    "resource_attrs",
    "scope_name",
    "scope_version",
    "attributes",
    "usage",
    "events",
    "links",
]


def _decoded_request_body(request) -> bytes:
    raw = request.body
    if "gzip" in request.headers.get("Content-Encoding", ""):
        try:
            return zlib.decompress(raw, 16 + zlib.MAX_WBITS)
        except zlib.error as exc:
            raise _MalformedBodyError(f"Invalid gzip body: {exc}") from exc
    return raw


class _MalformedBodyError(Exception):
    pass


@api_view(["POST"])
@permission_classes([IsAuthenticated])
@extend_schema(exclude=True)
def otlp_traces(request):
    try:
        body = _decoded_request_body(request)
    except _MalformedBodyError as exc:
        return JsonResponse({"error": str(exc)}, status=400)

    try:
        export_request = trace_service_pb2.ExportTraceServiceRequest()
        export_request.ParseFromString(body)
    except Exception as exc:
        logger.error("Failed to parse OTLP protobuf: %s", exc)
        return JsonResponse({"error": f"Invalid protobuf: {exc}"}, status=400)

    all_spans: list[Span] = []
    for resource_span in export_request.resource_spans:
        outcome = _build_spans_for_resource(request, resource_span)
        if isinstance(outcome, HttpResponseBase):
            return outcome
        spans, project = outcome
        if project is None:
            continue
        all_spans.extend(spans)

    if all_spans:
        with transaction.atomic():  # bulk-upsert spans (idempotent on ``span_id``)
            Span.objects.bulk_create(
                all_spans,
                update_conflicts=True,
                unique_fields=["span_id"],
                update_fields=_SPAN_UPSERT_FIELDS,
            )

    # Spans are already persisted, so one poison span must not 500 the batch:
    # OTel exporters retry a 500 forever, re-ingesting the same payload.
    post_processed = 0
    for span in all_spans:
        try:
            process_span(span.span_id, str(span.project_id))
            post_processed += 1
        except Exception:
            logger.exception("post-processing failed for span %s", span.span_id)

    try:
        _update_evidence_profiles(all_spans)
    except Exception:  # noqa: BLE001 — profile grading must never fail ingest
        logger.warning("evidence profile update failed", exc_info=True)

    enqueue_trace_scoring(all_spans)

    return JsonResponse(
        {
            "spans_ingested": len(all_spans),
            "spans_post_processed": post_processed,
        }
    )


def _update_evidence_profiles(spans: list[Span]) -> None:
    """One hysteresis-damped EvidenceProfile update per capability per batch. Capability
    ids are re-read: the in-memory batch objects predate ``process_span``'s assignment."""
    from overbae.services.eval import profiles

    if not spans:
        return
    capability_by_span = dict(
        Span.objects.filter(span_id__in=[s.span_id for s in spans])
        .exclude(capability__isnull=True)
        .values_list("span_id", "capability_id")
    )
    by_capability: dict[Any, list[Span]] = {}
    for span in spans:
        capability_id = capability_by_span.get(span.span_id)
        if capability_id is not None:
            by_capability.setdefault(capability_id, []).append(span)
    for capability_id, capability_spans in by_capability.items():
        profiles.observe_batch(capability_id, capability_spans)


def enqueue_trace_scoring(spans: list[Span]) -> None:
    """Fire trace scoring for each root span landed in this batch.

    Keyed on the root span because in OTel the root ends last, so its arrival
    signals the trace is complete enough to score. ``score_trace`` is idempotent
    and does its own capability/eval-set gating, so a re-fire is a cheap no-op. A
    broker hiccup must never fail ingest — durability outranks scoring.

    Also called by connector sync: ``bulk_create`` fires no signals, so imports
    have no other enqueue path and the beat sweep only looks back 2h.
    """
    from overbae.tasks.trace_scoring import score_trace

    seen: set[tuple[str, str]] = set()
    for span in spans:
        if span.parent_span_id is not None:
            continue
        key = (span.trace_id, str(span.project_id))
        if key in seen:
            continue
        seen.add(key)
        try:
            score_trace.delay(trace_id=span.trace_id, project_id=str(span.project_id))
        except Exception:  # noqa: BLE001 — scoring is best-effort; ingest must not fail
            logger.warning("Failed to enqueue live scoring for trace %s", span.trace_id)
