"""Ordering helpers for span/session columns that live in ``attributes`` JSON.

Tokens / cost / model / score-count are not real columns; they are annotated as
SQL before ``OrderingFilter`` runs. Key precedence here must stay aligned with
``serializers._*_from_span_attributes`` and ``SpanFilter``.
"""

from __future__ import annotations

from collections.abc import Callable

from django.db import connection
from django.db.models import F
from django.db.models.expressions import RawSQL

from overbae.models import Conversation, Span

SPAN_USAGE_ORDERING_FIELDS = frozenset({"total_tokens", "total_cost", "model", "trace_scores"})
SESSION_USAGE_ORDERING_FIELDS = frozenset({"total_tokens", "total_cost", "model", "timespan"})

_MODEL_KEYS = (
    "genai.model",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "genai.response.model",
    "llm.model",
    "model",
)

_TOTAL_TOKEN_KEYS = (
    "genai.total_tokens",
    "genai.usage.total_tokens",
    "llm.usage.total_tokens",
    "gen_ai.usage.total_tokens",
)
_PROMPT_TOKEN_KEYS = (
    "genai.prompt_tokens",
    "genai.usage.prompt_tokens",
    "gen_ai.usage.input_tokens",
)
_COMPLETION_TOKEN_KEYS = (
    "genai.completion_tokens",
    "genai.usage.completion_tokens",
    "gen_ai.usage.output_tokens",
)
_TOKEN_KEYS = _TOTAL_TOKEN_KEYS + _PROMPT_TOKEN_KEYS + _COMPLETION_TOKEN_KEYS

_COST_KEYS = (
    "cost",
    "response_cost",
    "gen_ai.usage.cost",
    "genai.cost",
    "overmind.cost",
)
# Namespaces only a GenAI instrumentation writes: OTel semconv and Traceloop/LangChain.
_GENAI_ATTR_PREFIXES = ("gen_ai.", "llm.")

# The semconv reuses ``gen_ai.operation.name`` for tool invocations, which ingest
# classifies as ``tool_call``, so it must never count as GenAI evidence on its own.
_TOOL_OPERATION = "execute_tool"
_OPERATION_NAME_KEY = "gen_ai.operation.name"


def _attrs_ref(alias: str) -> str:
    return f"{alias}.attributes" if alias else "attributes"


def _needed_fields(ordering: str, allowed: frozenset[str]) -> set[str]:
    return {part.lstrip("-") for part in ordering.split(",") if part.strip()} & allowed


def _text_expr(attrs: str, key: str) -> str:
    if connection.vendor == "postgresql":
        return f"NULLIF({attrs}->>'{key}','')"
    return f"NULLIF(json_extract({attrs}, '$.\"{key}\"'), '')"


def _numeric_expr(attrs: str, key: str) -> str:
    if connection.vendor == "postgresql":
        return f"NULLIF({attrs}->>'{key}','')::double precision"
    return f"CAST(json_extract({attrs}, '$.\"{key}\"') AS REAL)"


def _coalesce_numeric(attrs: str, keys: tuple[str, ...], *fallbacks: str) -> str:
    parts = [_numeric_expr(attrs, key) for key in keys] + list(fallbacks)
    return "COALESCE(" + ", ".join(parts) + ")"


def llm_total_tokens_sql(alias: str = "") -> str:
    """Floored at 0 when nothing is reported, so it cannot serve as a "did this
    span report usage?" test — use :func:`genai_evidence_predicate`."""
    attrs = _attrs_ref(alias)
    prompt = _coalesce_numeric(attrs, _PROMPT_TOKEN_KEYS, "0")
    completion = _coalesce_numeric(attrs, _COMPLETION_TOKEN_KEYS, "0")
    return _coalesce_numeric(attrs, _TOTAL_TOKEN_KEYS, f"{prompt} + {completion}")


def llm_cost_sql(alias: str = "") -> str:
    return _coalesce_numeric(_attrs_ref(alias), _COST_KEYS, "0")


def llm_model_sql(alias: str = "") -> str:
    attrs = _attrs_ref(alias)
    return "COALESCE(" + ", ".join(_text_expr(attrs, key) for key in _MODEL_KEYS) + ")"


def usage_sql(per_span_sql: Callable[..., str], *, all_spans: bool) -> str:
    """Per row, or summed across the trace.

    The root-span list displays trace-wide totals, so ordering and filtering must
    use the same correlated SUM — the root span's own attributes are usually
    usage-free and would disagree with the number on screen.
    """
    if all_spans:
        return per_span_sql()
    table = Span._meta.db_table
    inner = per_span_sql("s")
    return (
        f"(SELECT COALESCE(SUM(({inner})), 0) FROM {table} s "
        f"WHERE s.trace_id = {table}.trace_id "
        f"AND s.project_id = {table}.project_id)"
    )


def _keys_present_predicate(attrs: str, keys: tuple[str, ...]) -> str:
    """Tests raw keys, never a coalesced total: those floor at 0, so
    ``… IS NOT NULL`` on one is always true."""
    return " OR ".join(f"{_text_expr(attrs, key)} IS NOT NULL" for key in keys)


def _model_present_predicate(alias: str) -> str:
    return _keys_present_predicate(_attrs_ref(alias), _MODEL_KEYS)


def _genai_namespace_predicate(attrs: str) -> str:
    """Catches LLM calls that never got far enough to report a model or tokens —
    auth rejection, connect timeout, cache short-circuit."""
    operation = _text_expr(attrs, _OPERATION_NAME_KEY)
    is_not_tool_call = f"COALESCE(LOWER(TRIM({operation})), '') <> '{_TOOL_OPERATION}'"
    if connection.vendor == "postgresql":
        matches = " OR ".join(
            f"LEFT(k.key, {len(prefix)}) = '{prefix}'" for prefix in _GENAI_ATTR_PREFIXES
        )
        # jsonb_object_keys() errors on a non-object, so normalise first.
        keys = (
            f"jsonb_object_keys(CASE WHEN jsonb_typeof({attrs}) = 'object' "
            f"THEN {attrs} ELSE '{{}}'::jsonb END) AS k(key)"
        )
    else:
        matches = " OR ".join(
            f"SUBSTR(k.key, 1, {len(prefix)}) = '{prefix}'" for prefix in _GENAI_ATTR_PREFIXES
        )
        keys = f"json_each({attrs}) AS k"
    return f"EXISTS (SELECT 1 FROM {keys} WHERE {matches}) AND {is_not_tool_call}"


def genai_evidence_predicate(alias: str = "") -> str:
    """Deliberately broader than "reported a model": a rejected key or a timeout
    leaves a span with no model and no tokens. The cheap scalar lookups come
    first so the prefix scan only runs when they miss.
    """
    attrs = _attrs_ref(alias)
    return (
        f"(({_keys_present_predicate(attrs, _MODEL_KEYS)}) "
        f"OR ({_keys_present_predicate(attrs, _TOKEN_KEYS)}) "
        f"OR ({_genai_namespace_predicate(attrs)}))"
    )


def _trace_scores_sql(table: str) -> str:
    """The Task Execution Score (``trace_scoring._execution.score``), so the
    Scores column orders by quality rather than chip count. NULL when unscored."""
    col = f"{table}.feedback_score"
    if connection.vendor == "postgresql":
        return f"""(
            CASE
                WHEN jsonb_typeof({col}->'trace_scoring'->'_execution'->'score') = 'number'
                    THEN ({col}->'trace_scoring'->'_execution'->>'score')::float8
                ELSE NULL
            END
        )"""
    return f"""(
        CASE
            WHEN json_type({col}, '$.trace_scoring._execution.score') IN ('integer', 'real')
                THEN json_extract({col}, '$.trace_scoring._execution.score')
            ELSE NULL
        END
    )"""


def annotate_spans_for_ordering(queryset, ordering: str, *, all_spans: bool):
    needed = _needed_fields(ordering, SPAN_USAGE_ORDERING_FIELDS)
    if not needed:
        return queryset

    table = Span._meta.db_table

    if "total_tokens" in needed:
        queryset = queryset.annotate(
            total_tokens=RawSQL(usage_sql(llm_total_tokens_sql, all_spans=all_spans), [])
        )

    if "total_cost" in needed:
        queryset = queryset.annotate(
            total_cost=RawSQL(usage_sql(llm_cost_sql, all_spans=all_spans), [])
        )

    if "model" in needed:
        if all_spans:
            queryset = queryset.annotate(model=RawSQL(llm_model_sql(), []))
        else:
            # Earliest span reporting a model — must match ``trace_usage_totals``.
            model_expr = llm_model_sql("s")
            present = _model_present_predicate("s")
            queryset = queryset.annotate(
                model=RawSQL(
                    f"(SELECT {model_expr} FROM {table} s "
                    f"WHERE s.trace_id = {table}.trace_id "
                    f"AND s.project_id = {table}.project_id "
                    f"AND ({present}) "
                    f"ORDER BY s.start_time_ns ASC LIMIT 1)",
                    [],
                )
            )

    if "trace_scores" in needed:
        queryset = queryset.annotate(trace_scores=RawSQL(_trace_scores_sql(table), []))

    return queryset


def annotate_sessions_for_ordering(queryset, ordering: str):
    needed = _needed_fields(ordering, SESSION_USAGE_ORDERING_FIELDS)
    if not needed:
        return queryset

    conv_table = Conversation._meta.db_table
    span_table = Span._meta.db_table

    if "total_tokens" in needed:
        inner = llm_total_tokens_sql("s")
        queryset = queryset.annotate(
            total_tokens=RawSQL(
                f"(SELECT COALESCE(SUM(({inner})), 0) FROM {span_table} s "
                f"WHERE s.conversation_id = {conv_table}.id)",
                [],
            )
        )

    if "total_cost" in needed:
        inner = llm_cost_sql("s")
        queryset = queryset.annotate(
            total_cost=RawSQL(
                f"(SELECT COALESCE(SUM(({inner})), 0) FROM {span_table} s "
                f"WHERE s.conversation_id = {conv_table}.id)",
                [],
            )
        )

    if "model" in needed:
        model_expr = llm_model_sql("s")
        present = _model_present_predicate("s")
        queryset = queryset.annotate(
            model=RawSQL(
                f"(SELECT {model_expr} FROM {span_table} s "
                f"WHERE s.conversation_id = {conv_table}.id "
                f"AND ({present}) "
                f"ORDER BY s.start_time_ns ASC LIMIT 1)",
                [],
            )
        )

    if "timespan" in needed:
        # ``first_span_ns`` / ``last_span_ns`` come from SessionViewSet.get_queryset.
        queryset = queryset.annotate(timespan=F("last_span_ns") - F("first_span_ns"))

    return queryset
