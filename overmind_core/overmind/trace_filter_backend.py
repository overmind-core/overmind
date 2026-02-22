"""
DRF-style filter backend for the span/trace listing query.

Filter format:  ``field;operator;value``

Each filter is a single query parameter value; repeat the param for multiple
conditions — all are AND-ed together.

Available fields
----------------
Span columns:
  operation           String  – span name / type
  status_code         Int     – raw OTel status (0=unset, 1=ok, 2=error)
  status              Alias   – "unset" | "ok" | "success" | "error"  (eq only)
  parent_span_id      String  – raw parent ID; use isnull for root/child checks
  prompt_id           String  – linked prompt ID
  duration_ms         Int     – span duration in milliseconds (computed)

Trace columns:
  application_name    String
  source              String

JSONB attribute paths (always treated as text):
  span_attr.<key>     – SpanModel.metadata_attributes[key]
  trace_attr.<key>    – TraceModel.metadata_attributes[key]

Supported operators
-------------------
  eq      field equals value
  neq     field does not equal value
  gt      field >  value
  gte     field >= value
  lt      field <  value
  lte     field <= value
  like    SQL LIKE  (use % as wildcard)
  ilike   SQL ILIKE (case-insensitive, use % as wildcard)
  in      field IN  comma-separated list
  notin   field NOT IN  comma-separated list
  isnull  field IS NULL when value=true, IS NOT NULL when value=false

Examples
--------
  ?filter=status;eq;error
  ?filter=status_code;eq;2
  ?filter=duration_ms;gte;100
  ?filter=operation;ilike;%chat%
  ?filter=parent_span_id;isnull;true
  ?filter=span_attr.gen_ai.request.model;eq;gpt-4o
  ?filter=source;in;overmind_api,langchain
  ?filter=application_name;ilike;my-%
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from sqlalchemy import cast, Text
from overmind_core.models.traces import SpanModel, TraceModel

SEP = ";"

# ---------------------------------------------------------------------------
# OTel status alias
# ---------------------------------------------------------------------------

_STATUS_ALIASES: dict[str, int] = {
    "unset": 0,
    "ok": 1,
    "success": 1,
    "error": 2,
}

# ---------------------------------------------------------------------------
# Field registry:  name → (column_expression, python_value_type)
# ---------------------------------------------------------------------------
# The column expressions are SQLAlchemy InstrumentedAttribute / BinaryExpression
# objects; they are safe to reference at module load time.

_FIELDS: dict[str, tuple[Any, type]] = {
    # ── SpanModel columns ────────────────────────────────────────────────────
    "operation": (SpanModel.operation, str),
    "status_code": (SpanModel.status_code, int),
    "parent_span_id": (SpanModel.parent_span_id, str),
    "prompt_id": (SpanModel.prompt_id, str),
    # UUID cast to text so ilike / eq work on trace IDs
    "trace_id": (cast(SpanModel.trace_id, Text), str),
    # Computed duration in milliseconds (integer division: ns → ms)
    "duration_ms": (
        (SpanModel.end_time_unix_nano - SpanModel.start_time_unix_nano) / 1_000_000,
        int,
    ),
    # JSONB columns cast to text for full-content search (ilike / eq / neq)
    "input_text": (cast(SpanModel.input, Text), str),
    "output_text": (cast(SpanModel.output, Text), str),
    # Serialised metadata_attributes for broad key+value search
    "metadata_text": (cast(SpanModel.metadata_attributes, Text), str),
    # ── TraceModel columns ───────────────────────────────────────────────────
    "application_name": (TraceModel.application_name, str),
    "source": (TraceModel.source, str),
}

_VALID_OPERATORS = frozenset(
    {"eq", "neq", "gt", "gte", "lt", "lte", "like", "ilike", "in", "notin", "isnull"}
)

# Human-readable list for error messages
AVAILABLE_FIELDS: str = ", ".join(
    sorted(_FIELDS) + ["span_attr.<key>", "trace_attr.<key>", "status"]
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_field(field: str) -> tuple[Any, type]:
    """
    Return ``(column_expr, value_type)`` for *field*.

    Raises ``HTTPException(422)`` for unknown fields.
    """
    if field in _FIELDS:
        return _FIELDS[field]

    if field.startswith("span_attr."):
        key = field[len("span_attr.") :]
        if not key:
            raise HTTPException(422, f"Empty key in span_attr filter: {field!r}")
        return SpanModel.metadata_attributes[key].astext, str

    if field.startswith("trace_attr."):
        key = field[len("trace_attr.") :]
        if not key:
            raise HTTPException(422, f"Empty key in trace_attr filter: {field!r}")
        return TraceModel.metadata_attributes[key].astext, str

    raise HTTPException(
        422,
        f"Unknown filter field {field!r}. Available fields: {AVAILABLE_FIELDS}",
    )


def _coerce(raw_value: str, value_type: type) -> Any:
    """Cast *raw_value* to *value_type*; raises ``HTTPException(422)`` on failure."""
    if raw_value == "null":
        return None
    if value_type is int:
        try:
            return int(raw_value)
        except ValueError:
            raise HTTPException(
                422, f"Expected an integer filter value, got {raw_value!r}"
            )
    return raw_value  # str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_condition(raw: str) -> Any:
    """
    Parse a single ``field;operator;value`` string and return a SQLAlchemy
    WHERE clause.

    Raises ``HTTPException(422)`` for any validation error so FastAPI surfaces
    it as a clean 422 Unprocessable Entity response.
    """
    parts = raw.split(SEP, 2)
    if len(parts) != 3:
        raise HTTPException(
            422,
            f"Invalid filter {raw!r}. "
            f"Expected format: field{SEP}operator{SEP}value  "
            f"(e.g. status_code{SEP}eq{SEP}2)",
        )

    field, op, value = (p.strip() for p in parts)
    op = op.lower()

    if op not in _VALID_OPERATORS:
        raise HTTPException(
            422,
            f"Invalid operator {op!r}. "
            f"Supported: {', '.join(sorted(_VALID_OPERATORS))}",
        )

    # ── Status alias (status;eq;error → status_code == 2) ───────────────────
    if field == "status":
        if op != "eq":
            raise HTTPException(
                422,
                f"The 'status' field only supports the 'eq' operator "
                f"(e.g. status{SEP}eq{SEP}error)",
            )
        if value not in _STATUS_ALIASES:
            raise HTTPException(
                422,
                f"Invalid status value {value!r}. "
                f"Supported: {', '.join(_STATUS_ALIASES)}",
            )
        return SpanModel.status_code == _STATUS_ALIASES[value]

    col, value_type = _resolve_field(field)

    # ── isnull ───────────────────────────────────────────────────────────────
    if op == "isnull":
        is_null = value.lower() in ("true", "1", "yes")
        return col.is_(None) if is_null else col.is_not(None)

    # ── in / notin ───────────────────────────────────────────────────────────
    if op in ("in", "notin"):
        items = [_coerce(v.strip(), value_type) for v in value.split(",")]
        return col.in_(items) if op == "in" else col.notin_(items)

    # ── Scalar comparison ─────────────────────────────────────────────────────
    coerced = _coerce(value, value_type)
    match op:
        case "eq":
            return col == coerced
        case "neq":
            return col != coerced
        case "gt":
            return col > coerced
        case "gte":
            return col >= coerced
        case "lt":
            return col < coerced
        case "lte":
            return col <= coerced
        case "like":
            return col.like(coerced)
        case "ilike":
            return col.ilike(coerced)

    # Unreachable – op is already validated above.
    raise HTTPException(422, f"Unhandled operator {op!r}")  # pragma: no cover


def build_conditions(raw_filters: list[str]) -> list[Any]:
    """Parse all filter strings and return a list of SQLAlchemy WHERE clauses."""
    return [build_condition(f) for f in raw_filters]
