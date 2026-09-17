"""Tier 0: compile capability cards into EvaluatorSpecs with no LLM calls.
``_fallback`` dataset cards skip pattern compilation: their semantic layer was
reconstructed deterministically, so only spine-derived evals are trustworthy.
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from django.utils import timezone

from overbae.services.eval.grounding import EvalGroundingContext
from overbae.services.eval.roles import roles_for_evaluator
from overbae.services.eval.specs import (
    AUTHORING_CONTRACT,
    TIER0_GENERATOR,
    EvaluatorSpec,
    SpecProvenance,
    mintable_claim_types,
)
from overbae.services.eval.surface_binding import (
    claim_needs_harness_runtime,
    has_generate_observable_constraint,
    split_generate_observability,
)

logger = logging.getLogger(__name__)


def stable_ref_id(text: Any) -> str:
    """sha1[:12] of the rule text. Stored constraint snapshots address checks
    by this id, so the digest must not change."""
    return hashlib.sha1(str(text or "").encode("utf-8")).hexdigest()[:12]


GENERATOR = TIER0_GENERATOR
FLOOR_GENERATOR = "card_floor@v1"
_FLOOR_ITEM_CAP = 8

# Exact checks own equality/format on a covered field; they cannot decide WHICH
# number in the source is the total (subtotal vs amount due).
_EXACT_COMPARISON_RE = re.compile(
    r"\b(equals?|equal to|matches?|matching|identical(?:ly)?|exactly|"
    r"YYYY-MM-DD|ISO[\s-]?8601)\b",
    re.I,
)
_SEMANTIC_FIELD_RE = re.compile(
    r"\b(subtotal|tax line|instead of|which (?:number|amount|value)|"
    r"wrong (?:number|amount|field))\b",
    re.I,
)
_CALIBRATION_RE = re.compile(r"\b(calibrat(?:e|ed|ion)|appropriate|warranted)\b", re.I)
_CONFIDENCE_FORMAT_RE = re.compile(
    r"\b(percent(?:age)?|normali[sz]e|format|range|0(?:\.0)?\s*[-–to]+\s*1)\b",
    re.I,
)
_JSON_CONTRACT_RE = re.compile(
    r"\b(?:valid(?:ly)?\s+json|json\s+valid(?:ity)?|parses?\s+as\s+json|"
    r"required[\s_-]+keys?|json[\s._-]?schema|parse_and_validate)\b",
    re.I,
)

# Tempered negation: matches the whole string only when <pattern> never occurs.
_NEGATED_REGEX_TEMPLATE = r"^(?:(?!{pattern})[\s\S])*$"


def negated_regex(pattern: str) -> str:
    return _NEGATED_REGEX_TEMPLATE.replace("{pattern}", pattern)


def _slug(text: str, max_len: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return slug[:max_len].rstrip("-") or "eval"


def _title(text: str, max_len: int = 200) -> str:
    words = re.sub(r"[_\-]+", " ", str(text or "")).strip()
    return (words[:1].upper() + words[1:])[:max_len] if words else ""


def _compilable_pattern(pattern: str, source: str) -> bool:
    try:
        re.compile(pattern)
    except re.error as exc:
        logger.warning("card_compiler: skipping invalid pattern from %s: %s", source, exc)
        return False
    return True


def _compile_all(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    construct = resolve_construct(grounding)
    specs: list[EvaluatorSpec] = []
    specs.extend(_compile_failure_modes(grounding))
    specs.extend(_compile_gate_signals(grounding))
    if construct["family"] == "contract":
        specs.extend(_compile_output_contract(grounding))
        specs.extend(_compile_reference_field_compare(grounding))
        specs.extend(_compile_canonical_fields(grounding))
        specs.extend(_compile_confidence_calibration(grounding))
        specs.extend(_compile_spine_constants(grounding))
    specs.extend(_compile_card_constraints(grounding))
    specs.extend(_compile_tool_surface(grounding))
    specs.extend(_compile_reference_rollups(grounding))
    specs.extend(_compile_format_gate(grounding))
    if construct["family"] == "open":
        specs.extend(_compile_bare_parse_gate(grounding, specs))
    return specs


def compile_card_evaluators(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    """What still needs CREATING: anything already materialized is dropped."""
    construct = resolve_construct(grounding)
    specs = _compile_all(grounding)
    allowed = mintable_claim_types(construct["family"])
    return _dedup(
        [s for s in specs if s.claim is None or s.claim.type in allowed],
        grounding.evaluator_inventory,
    )


_EXACT_KINDS = frozenset({"deterministic", "statistical"})


def deterministic_coverage(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    """Exactly-checkable specs, including ones already persisted.

    Not provenance-deduped. :func:`compile_card_evaluators` answers the opposite
    question — what still needs creating — and would hide the checks a judge
    must not re-author.
    """
    return [spec for spec in _compile_all(grounding) if spec.kind in _EXACT_KINDS]


def compile_managed_card_evaluators(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    """Behaviour suites plus the card-derived evaluators this construct
    warrants — cheap enough to reconcile on every card write. Same construct
    gate as :func:`_compile_all`. NO provenance dedup: the caller reconciles
    by identity, so an already-persisted evaluator still gets a missing
    ``trace_scoring`` membership backfilled."""
    construct = resolve_construct(grounding)
    specs = list(compile_behaviour_suites(grounding))
    if construct["family"] == "contract":
        specs.extend(_compile_output_contract(grounding))
        specs.extend(_compile_canonical_fields(grounding))
        specs.extend(_compile_confidence_calibration(grounding))
    specs.extend(_compile_card_constraints(grounding))
    specs.extend(_compile_label_accuracy(grounding))
    return specs


def compile_floor_judge(
    grounding: EvalGroundingContext, *, roles: list[str]
) -> list[EvaluatorSpec]:
    """Empty rather than a checklist-less judge: ``Evaluator.requires_checklist``
    refuses that at attach."""
    card = grounding.codebase_card
    if not card:
        return []
    covered = _coverage_field_names(grounding)
    kept: list[tuple[str, str]] = []
    for text in _plain_card_strings(card.get("failure_modes")):
        if _skip_floor_entry(text, covered):
            continue
        kept.append(("failure", text))
        if len(kept) >= _FLOOR_ITEM_CAP:
            break
    if len(kept) < _FLOOR_ITEM_CAP:
        for text in _plain_card_strings(card.get("success_criteria")):
            if _skip_floor_entry(text, covered):
                continue
            kept.append(("success", text))
            if len(kept) >= _FLOOR_ITEM_CAP:
                break
    if not kept:
        return []
    weight = 1.0 / len(kept)
    checklist: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    rubric_lines = ["Grade the output against the agent's capability-card criteria:", ""]
    for i, (kind, text) in enumerate(kept, start=1):
        item_id = _unique_slug(text, seen_ids)
        seen_ids.add(item_id)
        body = text.rstrip("?").rstrip()
        if kind == "failure":
            question = f"Does the output avoid this failure: {body}?"
        else:
            question = f"Does the output meet this criterion: {body}?"
        checklist.append({"id": item_id, "q": question, "weight": weight, "gate": False})
        rubric_lines.append(f"{i}. {text}")
    return [
        EvaluatorSpec(
            name="Card Criteria Compliance",
            kind="llm_judge",
            scope="final_output",
            score_type="numeric",
            requires_reference=False,
            rubric_md="\n".join(rubric_lines),
            checklist=checklist,
            variable_mapping=[
                {"var": "input", "source": "input"},
                {"var": "output", "source": "output"},
            ],
            applicable_roles=list(roles),
            provenance=_provenance(
                grounding,
                source="codebase_card.failure_modes",
                surface_area="failure_mode",
                generator=FLOOR_GENERATOR,
            ),
        )
    ]


def _plain_card_strings(value: Any) -> list[str]:
    return [s.strip() for s in (value or []) if isinstance(s, str) and s.strip()]


def _unique_slug(text: str, seen: set[str]) -> str:
    base = _slug(text)
    if base not in seen:
        return base
    n = 2
    while f"{base}-{n}" in seen:
        n += 1
    return f"{base}-{n}"


def _coverage_field_names(grounding: EvalGroundingContext) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for spec in deterministic_coverage(grounding):
        config = spec.config or {}
        for field in config.get("fields") or []:
            name = str(field.get("name") or "") if isinstance(field, dict) else ""
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        for key in config.get("required_keys") or []:
            name = str(key)
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        pred = str(config.get("prediction_field") or "")
        if pred and pred not in seen:
            seen.add(pred)
            names.append(pred)
    return names


def _skip_floor_entry(text: str, covered: list[str]) -> bool:
    if _JSON_CONTRACT_RE.search(text):
        return True
    if (
        _SELF_REPORTED_RE.search(text)
        and _CALIBRATION_RE.search(text)
        and not _CONFIDENCE_FORMAT_RE.search(text)
    ):
        return True
    if _named_fields(text, covered) and _EXACT_COMPARISON_RE.search(text):
        return not _SEMANTIC_FIELD_RE.search(text)
    return False


# A self-reported confidence is a number with no correct value to compare a
# reference against, so it is never routed to a deterministic check.
_SELF_REPORTED_RE = re.compile(r"confidence|certainty|probability|likelihood", re.I)
_DATE_FORMAT_RE = re.compile(r"YYYY-MM-DD|ISO[\s-]?8601", re.I)
_NUMBER_RE = re.compile(r"^\s*(number|integer|int|float|decimal)\b", re.I)
# Read from the declaration, not the field name: a field called ``summary``
# whose declaration is an identifier is still an exact compare.
_PROSE_RE = re.compile(
    r"\b(?:summary|description|explanation|rationale|notes)\b|"
    r"\bsentence\b|\bparagraph\b|\bfree text\b|"
    r"\bmarkdown\b|\blong-form\b|\bfree-form\b|\breport body\b",
    re.I,
)


def canonical_field_kind(name: str, declaration: str) -> str | None:
    """The exact comparison a declared output field admits, or ``None`` when only
    a judge can grade it.

    A plain string is trimmed case-insensitive equality — the compare a judge
    authors for an identifier and executes unreliably. Free prose (a
    declaration that describes a summary, description, explanation, rationale,
    notes, sentence/paragraph, markdown, or long-form report) has no single
    correct string and stays with the judge. A declared ``enum:`` / JSON-schema
    ``enum`` is a closed set.
    """
    decl = str(declaration or "").strip()
    if _SELF_REPORTED_RE.search(name) or _SELF_REPORTED_RE.search(decl):
        return None
    lead = decl.split("—")[0]
    if lead.strip().lower().startswith("bool"):
        return "boolean"
    if _NUMBER_RE.match(lead):
        return "number"
    # Explicit enum only. A prose list of values is not a declared closed set.
    if re.search(r"\benum\s*:", decl, re.I):
        return "enum"
    if lead.strip().lower().startswith("string"):
        if _DATE_FORMAT_RE.search(decl):
            return "date"
        if _PROSE_RE.search(decl):
            return None
        return "string"
    return None


def _declaration_text(declaration: Any) -> str:
    """Flatten a card field to the string ``canonical_field_kind`` reads.

    A JSON-schema ``enum`` array is stamped ``enum: a|b|c`` so the kind router
    and situational trigger matcher see the same closed set as a prose
    ``enum:`` declaration. A dict without ``enum`` keeps its description/type.
    """
    if isinstance(declaration, dict):
        desc = str(declaration.get("description") or declaration.get("type") or "").strip()
        raw_enum = declaration.get("enum")
        if isinstance(raw_enum, list):
            values = [
                str(v).strip() for v in raw_enum if str(v).strip() and " " not in str(v).strip()
            ]
            if values:
                stamped = "enum: " + "|".join(values)
                return f"{stamped} — {desc}" if desc else stamped
        return desc
    return str(declaration or "")


def canonical_output_fields(card: dict[str, Any] | None) -> list[dict[str, str]]:
    """Declared output fields that admit an exact comparison, in card order."""
    declared = (card or {}).get("output_fields") or {}
    if not isinstance(declared, dict):
        declared = {}
    props = ((card or {}).get("output_schema") or {}).get("properties") or {}
    if not isinstance(props, dict):
        props = {}
    routed = []
    seen: set[str] = set()
    for name in list(declared) + [k for k in props if k not in declared]:
        key = str(name).strip()
        if not key or key in seen:
            continue
        text = _declaration_text(declared[name]) if name in declared else ""
        kind = canonical_field_kind(key, text) if text else None
        if name in props:
            schema_kind = canonical_field_kind(key, _declaration_text(props[name]))
            if kind is None or (kind == "string" and schema_kind == "enum"):
                kind = schema_kind or kind
        if kind:
            routed.append({"name": key, "kind": kind})
            seen.add(key)
    return routed


def card_output_field_names(card: dict[str, Any] | None) -> list[str]:
    """The key set CodebaseSource materialises as ``schema_field`` nodes."""
    schema = (card or {}).get("output_schema") or {}
    keys = [str(k) for k in (schema.get("properties") or {})]
    keys += [str(k) for k in schema.get("required_keys") or [] if str(k) not in keys]
    if not keys:
        keys = [str(k) for k in (card or {}).get("output_fields") or {}]
    return [k for k in keys if k.strip()]


def card_tool_names(card: dict[str, Any] | None) -> list[str]:
    return [
        str(t.get("name")).strip()
        for t in (card or {}).get("tool_spec") or []
        if isinstance(t, dict) and str(t.get("name") or "").strip()
    ]


def _field_mentioned(name: str, text: str) -> bool:
    # Underscore is part of an identifier, so `decision` does not match inside
    # `post_decision`. A bound ``field`` means this item grades that output key.
    return bool(
        re.search(rf"(?<![A-Za-z0-9_]){re.escape(name)}(?![A-Za-z0-9_])", text, re.IGNORECASE)
    )


def _named_fields(text: str, field_names: list[str]) -> list[str]:
    hits = [f for f in field_names if _field_mentioned(f, text)]
    # Longest name wins: `text` and `extraction_text` must not double-count.
    return [h for h in hits if not any(h != other and _field_mentioned(h, other) for other in hits)]


_TRAJECTORY_ITEM_RE = re.compile(
    r"_called_|_before_|_after_|tool call|\bcalled\b|\bcalls\b|\bcalling\b",
    re.I,
)


def _item_is_trajectory(item: dict[str, Any], tool_names: list[str]) -> bool:
    text = f"{item.get('id') or ''} {item.get('q') or ''}"
    if any(_field_mentioned(t, text) for t in tool_names):
        return True
    return bool(_TRAJECTORY_ITEM_RE.search(text))


def bind_checklist_fields(
    checklist: list[dict[str, Any]],
    field_names: list[str],
    *,
    tool_names: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Schema-driven, never LLM-driven: *field_names* is the only candidate
    vocabulary. A declared ``field`` that does not resolve is dropped with a
    warning, since an unknown ref would emit a violates edge to a node that
    cannot exist. An item without one gets a ``field`` only when its id or
    question names exactly ONE schema field as a whole identifier, and the item
    is not about the tool loop.

    Pure and idempotent, so compile, sync and re-sync converge on the same
    bindings.
    """
    if not field_names:
        return list(checklist)
    known = set(field_names)
    tools = [t for t in (tool_names or []) if t]
    bound: list[dict[str, Any]] = []
    for item in checklist:
        if not isinstance(item, dict):
            bound.append(item)
            continue
        item = dict(item)
        declared = str(item.get("field") or "").strip()
        if declared:
            if declared.removeprefix("output_schema.") not in known:
                logger.warning(
                    "bind_checklist_fields: checklist item %r declares unknown field %r "
                    "(card output fields: %s) — dropping the binding",
                    item.get("id"),
                    declared,
                    sorted(known),
                )
                item["field"] = ""
        elif not _item_is_trajectory(item, tools):
            named = _named_fields(f"{item.get('id') or ''} {item.get('q') or ''}", field_names)
            if len(named) == 1:
                item["field"] = named[0]
        bound.append(item)
    return bound


_SITUATIONAL_ITEM_RE = re.compile(r"\b(?:if|when|unless|whenever)\b", re.I)


def drop_exactly_covered_items(
    checklist: list[dict[str, Any]], exact_field_names: list[str]
) -> tuple[list[dict[str, Any]], list[str]]:
    """Drop items whose bound field a deterministic check already owns.

    Situational “if this, then field = value” items stay — that is a different
    claim from teacher equality.
    """
    exact = {n.lower() for n in exact_field_names if n}
    if not exact:
        return list(checklist), []
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for item in checklist:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        field = str(item.get("field") or "").strip().removeprefix("output_schema.")
        text = f"{item.get('id') or ''} {item.get('q') or ''}"
        if field and field.lower() in exact and not _SITUATIONAL_ITEM_RE.search(text):
            dropped.append(str(item.get("id") or field))
            continue
        kept.append(item)
    return kept, dropped


_EQ_VERB_RE = re.compile(r"\b(?:equals?|equal to|matches|match)\b", re.I)


def is_pure_reference_equality(
    item: dict[str, Any],
    field_names: list[str],
    *,
    tool_names: list[str] | None = None,
) -> bool:
    """True when the item is only ``{output.X} == {reference.X}`` for one field.

    Situational items (if/when this, then that) and tool-loop items stay. A
    bare-string field is not exactly-checkable, but restating teacher equality
    in a judge still triple-counts the same construct.
    """
    if not isinstance(item, dict):
        return False
    if _item_is_trajectory(item, tool_names or []):
        return False
    text = f"{item.get('id') or ''} {item.get('q') or ''}"
    named = _named_fields(text, field_names)
    field = str(item.get("field") or "").strip().removeprefix("output_schema.")
    target = field or (named[0] if len(named) == 1 else "")
    if not target:
        return False
    if named and named != [target]:
        return False
    if not (
        re.search(rf"\{{output\.{re.escape(target)}\}}", text)
        and re.search(rf"\{{reference\.{re.escape(target)}\}}", text)
    ):
        return False
    return bool(_EQ_VERB_RE.search(text))


def drop_pure_reference_equality(
    checklist: list[dict[str, Any]],
    field_names: list[str],
    *,
    tool_names: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for item in checklist:
        if isinstance(item, dict) and is_pure_reference_equality(
            item, field_names, tool_names=tool_names
        ):
            dropped.append(str(item.get("id") or item.get("field") or ""))
            continue
        kept.append(item)
    return kept, dropped


_ONCE_RE = re.compile(
    r"(?<![A-Za-z0-9])(exactly )?once(?![A-Za-z0-9])|(?<![A-Za-z0-9])not omitted(?![A-Za-z0-9])",
    re.I,
)
_LAST_RE = re.compile(r"(?<![A-Za-z0-9])last(?![A-Za-z0-9])", re.I)
_STOP_AFTER_RE = re.compile(
    r"(?<![A-Za-z0-9])stop(?![A-Za-z0-9]).{0,40}(?<![A-Za-z0-9])after(?![A-Za-z0-9])|"
    r"(?<![A-Za-z0-9])after(?![A-Za-z0-9]).{0,40}(?<![A-Za-z0-9])stop(?![A-Za-z0-9])",
    re.I,
)
_BEFORE_RE = re.compile(r"(?<![A-Za-z0-9])before(?![A-Za-z0-9])", re.I)
_ARGS_RE = re.compile(
    r"\bcorrect_tool_arguments\b|\bwell-formed args\b|"
    r"\b(arguments?|args)\b.{0,60}\b(correct|well[- ]formed|malformed|expected|schema|keys)\b|"
    r"\b(correct|well[- ]formed|malformed|expected)\b.{0,60}\b(arguments?|args)\b",
    re.I,
)
_JSON_FORMAT_RE = re.compile(
    r"\bjson only\b|\bmarkdown fence\b|\bno markdown\b|\breturn json\b", re.I
)


def _item_text(item: dict[str, Any]) -> str:
    return f"{item.get('id') or ''} {item.get('q') or ''}"


def _entry_tool(entry: dict[str, Any]) -> str:
    params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
    raw = str(params.get("tool") or "").strip()
    if raw:
        return raw
    tools = [str(t).strip() for t in (entry.get("tools") or []) if str(t).strip()]
    return tools[0] if len(tools) == 1 else ""


def item_restates_constraint(item: dict[str, Any], entry: dict[str, Any]) -> bool:
    """True when the checklist item is the same mechanical claim *entry* already checks."""
    if not isinstance(item, dict) or not isinstance(entry, dict):
        return False
    text = _item_text(item)
    ctype = str(entry.get("type") or "")
    params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
    tool = _entry_tool(entry)
    if ctype == "tool_arguments":
        return bool(_ARGS_RE.search(text))
    if ctype == "output_format" and (
        str(params.get("format") or "").lower() == "json" or params.get("fence_output") is False
    ):
        return bool(_JSON_FORMAT_RE.search(text))
    if not tool or not _field_mentioned(tool, text):
        return False
    if ctype == "tool_discipline":
        limit = params.get("max_calls", params.get("max_tool_calls"))
        if isinstance(limit, (int, float)) and int(limit) == 1:
            return bool(_ONCE_RE.search(text))
        return False
    if ctype in ("ordering", "precondition") and params.get("position") in ("last", "first"):
        return bool(_LAST_RE.search(text) or _STOP_AFTER_RE.search(text))
    # Situational before: the item must name the trigger, not merely the tool
    # and the word "before" (a batching item says "lookups before post_decision").
    if not _BEFORE_RE.search(text):
        return False
    field = str(params.get("when_field") or "").strip()
    if field:
        values = [str(v) for v in (params.get("when_values") or []) if str(v).strip()]
        return _field_mentioned(field, text) or any(_field_mentioned(v, text) for v in values)
    pair = params.get("when_fields_differ")
    if isinstance(pair, list) and len(pair) == 2:
        return all(_field_mentioned(str(f), text) for f in pair)
    return False


def drop_constraint_restatements(
    checklist: list[dict[str, Any]], card: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[str]]:
    entries = constraint_entries(card)
    if not entries:
        return list(checklist), []
    kept: list[dict[str, Any]] = []
    dropped: list[str] = []
    for item in checklist:
        if isinstance(item, dict) and any(
            item_restates_constraint(item, entry) for entry in entries
        ):
            dropped.append(str(item.get("id") or item.get("q") or ""))
            continue
        kept.append(item)
    return kept, dropped


def prepare_judge_checklist(
    checklist: list[dict[str, Any]], card: dict[str, Any] | None
) -> tuple[list[dict[str, Any]], list[str]]:
    """Bind schema fields, then drop items a deterministic check already owns,
    items that only restate ``output.X == reference.X``, and items that restate
    a compiled constraint."""
    names = card_output_field_names(card)
    tools = card_tool_names(card)
    bound = bind_checklist_fields(checklist, names, tool_names=tools)
    exact = [str(f["name"]) for f in canonical_output_fields(card)]
    kept, dropped_exact = drop_exactly_covered_items(bound, exact)
    kept, dropped_eq = drop_pure_reference_equality(kept, names, tool_names=tools)
    kept, dropped_con = drop_constraint_restatements(kept, card)
    return kept, dropped_exact + dropped_eq + dropped_con


def _semantic_card(grounding: EvalGroundingContext) -> dict[str, Any] | None:
    card = grounding.dataset_card
    if not card or card.get("_fallback"):
        return None
    return card


def _provenance(
    grounding: EvalGroundingContext,
    *,
    source: str,
    surface_area: str,
    baseline: float | None = None,
    blocked_on: list[str] | None = None,
    generator: str = GENERATOR,
) -> SpecProvenance:
    return SpecProvenance(
        source=source,
        data_version=grounding.data_version,
        codebase_commit=grounding.codebase_commit,
        baseline_match_rate=baseline,
        generator=generator,
        authoring_contract=AUTHORING_CONTRACT,
        blocked_on=blocked_on or [],
        surface_area=surface_area,
    )


def _structural_detection_config(detection: Any) -> dict[str, Any] | None:
    if not isinstance(detection, dict):
        return None
    kind = str(detection.get("kind") or "").strip()
    field = str(detection.get("field") or "").strip()
    if not field:
        return None
    if kind == "missing_field":
        return {"check": "field_present", "field": field}
    if kind == "enum_violation":
        allowed = [str(v) for v in detection.get("allowed") or [] if str(v).strip()]
        if allowed:
            return {
                "check": "schema_field_conformance",
                "fields": {field: {"enum": allowed}},
                "applies_when": {"output_present": True},
            }
    return None


def _compile_failure_modes(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    card = _semantic_card(grounding)
    if card is None:
        return []
    specs: list[EvaluatorSpec] = []
    for i, mode in enumerate(card.get("failure_modes") or []):
        if not isinstance(mode, dict):
            continue
        source = f"dataset_card.failure_modes[{i}]"
        description = str(mode.get("description") or "").strip()
        # Structured detection wins; regex stays for genuinely textual failures.
        config = _structural_detection_config(mode.get("detection"))
        if config is None:
            pattern = str(mode.get("detection_pattern") or "").strip()
            if not pattern or not _compilable_pattern(pattern, source):
                continue
            config = {"check": "regex", "pattern": negated_regex(pattern)}
        specs.append(
            EvaluatorSpec(
                name=f"no-{_slug(description)}",
                display_name=f"No failure: {description[:180]}" if description else "Failure gate",
                description=f'Regression gate for failure mode: "{description}"',
                kind="deterministic",
                scope="final_output",
                score_type="boolean",
                config=config,
                provenance=_provenance(
                    grounding,
                    source=source,
                    surface_area="failure_mode",
                    baseline=mode.get("baseline_match_rate"),
                ),
            )
        )
    return specs


def _compile_gate_signals(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    card = _semantic_card(grounding)
    if card is None:
        return []
    specs: list[EvaluatorSpec] = []
    for i, signal in enumerate(card.get("quality_signals") or []):
        if not isinstance(signal, dict) or signal.get("severity") != "gate":
            continue
        pattern = str(signal.get("detection_pattern") or "").strip()
        source = f"dataset_card.quality_signals[{i}]"
        if not pattern or not _compilable_pattern(pattern, source):
            continue
        text = str(signal.get("signal") or "").strip()
        specs.append(
            EvaluatorSpec(
                name=f"gate-{_slug(text)}",
                display_name=f"Quality gate: {text[:180]}" if text else "Quality gate",
                description=f'Hard gate for quality signal: "{text}"',
                kind="deterministic",
                scope="final_output",
                score_type="boolean",
                config={"check": "regex", "pattern": negated_regex(pattern)},
                provenance=_provenance(
                    grounding,
                    source=source,
                    surface_area="failure_mode",
                    baseline=signal.get("baseline_match_rate"),
                ),
            )
        )
    return specs


def _expected_output_example(card: dict[str, Any]) -> Any:
    expected = card.get("expected_output")
    if not isinstance(expected, dict):
        return None
    return expected.get("example")


def _output_contract_kind(card: dict[str, Any]) -> Literal["json_object", "scalar_string", "none"]:
    required_keys = [
        str(k)
        for k in ((card.get("output_schema") or {}).get("required_keys") or [])
        if str(k).strip()
    ]
    example = _expected_output_example(card)
    if required_keys:
        return "json_object"
    if isinstance(example, dict) and example:
        return "json_object"
    if isinstance(example, str) and example.strip():
        return "scalar_string"
    return "none"


def _construct_contract_keys(card: dict[str, Any]) -> list[str]:
    """Keys of the model-layer envelope, not harness sidecar ``output_fields``.

    ``output_fields`` is the assembled capability record; counting those as the
    JSON contract flips a markdown report into extraction.
    """
    required = [
        str(k)
        for k in ((card.get("output_schema") or {}).get("required_keys") or [])
        if str(k).strip()
    ]
    if required:
        return required
    example = _expected_output_example(card)
    if isinstance(example, dict):
        return [str(k) for k in example if str(k).strip()]
    return []


CONSTRUCT_CONTRACT_TASK_TYPES = frozenset({"classification", "extraction", "tool_calling"})

_STRUCTURED_FIELD_TYPES = frozenset({"array", "object", "integer", "number", "boolean"})

_CONSTRUCT_LABEL_CUES: tuple[tuple[str, str], ...] = (
    (r"summar", "summarization"),
    # Word-bounded: "history" contains "story".
    (r"\b(creative|story|stories|poem|fiction)\b", "creative_writing"),
    (r"classif|categor|triage|\brout", "classification"),
    (r"extract", "extraction"),
)


def _construct_label_from_prose(card: dict[str, Any], default: str) -> str:
    text = " ".join(str(card.get(k) or "") for k in ("task", "domain")).lower()
    for pattern, label in _CONSTRUCT_LABEL_CUES:
        if re.search(pattern, text):
            return label
    return default


def _declared_field_type(meta: Any, example_value: Any) -> tuple[str | None, bool]:
    if isinstance(meta, str):
        field_type, _ = _prose_leading_type(meta)
        return field_type, bool(_prose_enum(meta))
    if isinstance(meta, dict):
        field_type = _PROSE_TYPE_TOKENS.get(str(meta.get("type") or "").lower())
        if field_type is None and isinstance(meta.get("description"), str):
            field_type, _ = _prose_leading_type(meta["description"])
        return field_type, bool(meta.get("enum"))
    if isinstance(example_value, bool):
        return "boolean", False
    if isinstance(example_value, (int, float)):
        return "number", False
    if isinstance(example_value, list):
        return "array", False
    if isinstance(example_value, dict):
        return "object", False
    if isinstance(example_value, str):
        return "string", False
    return None, False


def _dataset_task_type(grounding: EvalGroundingContext) -> tuple[str, str]:
    """The strongest construct signal when a dataset exists."""
    dataset = grounding.dataset
    if dataset is None or getattr(dataset, "pk", None) is None:
        return "", ""
    try:
        context = dataset.context
    except Exception:  # noqa: BLE001 — missing context row means no signal
        return "", ""
    task_type = str(getattr(context, "task_type", "") or "").strip().lower()
    if not task_type:
        return "", ""
    return task_type, f"dataset_{getattr(context, 'task_type_source', '') or 'heuristic'}"


def _card_construct(card: dict[str, Any]) -> dict[str, str]:
    """A JSON envelope whose required fields are mostly free prose is OPEN even
    though it parses: per-field gates on narration are phantom checks."""
    if _output_contract_kind(card) != "json_object":
        return {
            "construct": _construct_label_from_prose(card, "analytical"),
            "family": "open",
            "source": "card",
        }
    required = _construct_contract_keys(card)
    if not required:
        return {
            "construct": _construct_label_from_prose(card, "analytical"),
            "family": "open",
            "source": "card",
        }
    properties = (card.get("output_schema") or {}).get("properties") or {}
    output_fields = card.get("output_fields") or {}
    example = _expected_output_example(card)
    example = example if isinstance(example, dict) else {}
    structured: list[str] = []
    prose = 0
    enum_fields = 0
    for key in required:
        meta = properties.get(key) if isinstance(properties, dict) else None
        if meta is None:
            meta = output_fields.get(key)
        field_type, has_enum = _declared_field_type(meta, example.get(key))
        if has_enum:
            enum_fields += 1
            structured.append(key)
        elif field_type in _STRUCTURED_FIELD_TYPES:
            structured.append(key)
        elif field_type == "string":
            prose += 1
    if prose > len(structured):
        return {
            "construct": _construct_label_from_prose(card, "analytical"),
            "family": "open",
            "source": "card",
        }
    if len(required) == 1 and enum_fields == 1:
        label = "classification"
    elif (
        len(structured) == 1
        # Whole-name match: "extractions" contains "action".
        and re.fullmatch(r"(tool_?)?(actions?|calls?|invocations?)", structured[0], re.IGNORECASE)
        and card.get("tool_spec")
    ):
        label = "tool_calling"
    else:
        label = _construct_label_from_prose(card, "extraction")
    return {"construct": label, "family": "contract", "source": "card"}


def classify_construct(grounding: EvalGroundingContext) -> dict[str, str]:
    """``family`` drives routing: ``contract`` compiles per-field gates, ``open`` none."""
    task_type, source = _dataset_task_type(grounding)
    if task_type:
        family = "contract" if task_type in CONSTRUCT_CONTRACT_TASK_TYPES else "open"
        return {"construct": task_type, "family": family, "source": source}
    card = grounding.codebase_card
    if not card:
        # Without a card no per-field gates are minted anyway.
        return {"construct": "unknown", "family": "contract", "source": "default"}
    return _card_construct(card)


def resolve_construct(grounding: EvalGroundingContext, *, persist: bool = False) -> dict[str, str]:
    """A dataset-derived stamp is reused; a card-sourced stamp is recomputed."""
    capability = grounding.capability or getattr(grounding.dataset, "capability", None)
    stored = None
    if capability is not None:
        stored = (getattr(capability, "improvement_metadata", None) or {}).get("eval_construct")
    fresh = classify_construct(grounding)
    # Dataset-derived stamps stay; a card-sourced stamp is recomputed so a
    # classifier fix lands without a new dataset signal.
    if (
        isinstance(stored, dict)
        and stored.get("construct")
        and stored.get("family")
        and str(stored.get("source") or "").startswith("dataset_")
    ):
        return {k: str(stored.get(k) or "") for k in ("construct", "family", "source")}
    if persist and capability is not None and getattr(capability, "pk", None) is not None:
        meta = dict(capability.improvement_metadata or {})
        meta["eval_construct"] = {**fresh, "classified_at": timezone.now().isoformat()}
        capability.improvement_metadata = meta
        capability.save(update_fields=["improvement_metadata"])
    return fresh


def _compile_output_contract(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    card = grounding.codebase_card
    if not card:
        return []
    kind = _output_contract_kind(card)
    if kind == "scalar_string":
        return []
    if kind == "none":
        return []
    required = _construct_contract_keys(card)
    if not required:
        return []
    return [
        EvaluatorSpec(
            name="output-contract-required-keys",
            display_name="Output contract: required fields present",
            description=(
                "Output parses as JSON and carries every contract field declared by the "
                "capability's capability card."
            ),
            kind="deterministic",
            scope="final_output",
            score_type="boolean",
            # A two-layer harness reshapes the raw extraction schema, so this
            # gate stays off live trace scoring.
            surface="model",
            config={
                "check": "json_schema_valid",
                "required_keys": required,
                "gate_only": True,
            },
            provenance=_provenance(
                grounding, source="codebase_card.output_fields", surface_area="output_contract"
            ),
        )
    ]


def _compile_canonical_fields(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    """One deterministic evaluator over the declared fields that have a single
    correct value.

    A judge grading an exactly-checkable field is both slower and less accurate:
    measured against the reference, the judges agreed with the facts at only
    r≈0.55–0.59, while costing a call per field. Free prose stays with the judge.
    """
    card = grounding.codebase_card
    if not card:
        return []
    fields = canonical_output_fields(card)
    contract_keys = set(_construct_contract_keys(card))
    if contract_keys:
        fields = [f for f in fields if f["name"] in contract_keys]
    if not fields:
        return []
    return [
        EvaluatorSpec(
            name="output-field-accuracy",
            description=(
                "Fraction of the agent's exactly-checkable output fields that match the "
                "reference, or the source input where the reference is silent."
            ),
            kind="deterministic",
            scope="final_output",
            score_type="numeric",
            requires_reference=True,
            # Grades the raw model extraction, which a two-layer harness reshapes.
            surface="model",
            config={"check": "canonical_fields", "fields": fields},
            provenance=_provenance(
                grounding, source="codebase_card.output_fields", surface_area="output_contract"
            ),
        )
    ]


def _compile_confidence_calibration(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    """Whether a self-reported confidence is honest, scored over the whole run.

    Needs a deterministic field check to say whether each row was actually
    correct, so it is only minted alongside one. A single prediction cannot be
    calibrated, which is why this is a run-level statistical metric rather than
    a per-sample item — grading confidence per sample against a golden is the
    defect that produced two false results in earlier iterations.
    """
    card = grounding.codebase_card
    if not card or not canonical_output_fields(card):
        return []
    declared = card.get("output_fields") or {}
    field = next(
        (
            str(name)
            for name, decl in declared.items()
            if _SELF_REPORTED_RE.search(str(name)) or _SELF_REPORTED_RE.search(str(decl))
        ),
        "",
    )
    if not field:
        return []
    return [
        EvaluatorSpec(
            name="confidence-calibration",
            description=(
                "Whether the agent's stated confidence matches how often it is actually "
                "right, measured across the run."
            ),
            kind="statistical",
            scope="dataset",
            score_type="numeric",
            requires_reference=True,
            surface="model",
            config={
                "metric": "calibration",
                "prediction_field": field,
                "reference_evaluator": "output-field-accuracy",
                # A calibration figure over a handful of rows is noise.
                "min_n": 10,
            },
            provenance=_provenance(
                grounding, source="codebase_card.output_fields", surface_area="output_contract"
            ),
        )
    ]


_POSITION_RE = re.compile(r"\b(last|first)\b", re.I)
_SITUATIONAL_RE = re.compile(r"\b(before|after|when|if|unless|whenever)\b", re.I)


def schema_field_map(card: dict[str, Any] | None) -> dict[str, str]:
    """Name → declaration for every input/output field the card names."""
    out: dict[str, str] = {}
    for src in ((card or {}).get("output_fields"), (card or {}).get("input_schema")):
        if not isinstance(src, dict):
            continue
        for name, declaration in src.items():
            key = str(name).strip()
            if key:
                out[key] = _declaration_text(declaration)
    schema = (card or {}).get("output_schema") or {}
    props = schema.get("properties") if isinstance(schema, dict) else {}
    if isinstance(props, dict):
        for name, declaration in props.items():
            key = str(name).strip()
            if not key:
                continue
            text = _declaration_text(declaration)
            if key not in out:
                out[key] = text
            elif re.search(r"\benum\s*:", text) and not re.search(r"\benum\s*:", out[key]):
                # Properties.enum is the declared closed set; a bare output_fields string is not.
                out[key] = text
    return out


def _declared_enum_values(declaration: str) -> list[str]:
    match = re.search(r"\benum\s*:?\s*(.+)$", declaration, re.I)
    if not match:
        return []
    rest = re.split(r"[—]", match.group(1), maxsplit=1)[0]
    return [
        part.strip().strip("'\"`")
        for part in re.split(r"[|,/]", rest)
        if part.strip() and " " not in part.strip()
    ]


def situational_trigger_params(rule: str, schema: dict[str, str]) -> dict[str, Any]:
    """A one-tool 'before/when X' rule is checkable when X is a schema field or
    a declared enum value of exactly one field. Two mentioned fields of unknown
    relation stay uncompiled — guessing an inequality is a different claim.
    ``when_fields_differ`` is only honoured when the card already declared it.
    """
    mentioned = _named_fields(rule, list(schema))
    if len(mentioned) == 1:
        return {"when_field": mentioned[0]}
    if len(mentioned) >= 2:
        return {}
    matched: list[tuple[str, list[str]]] = []
    for name, declaration in schema.items():
        hits = [v for v in _declared_enum_values(declaration) if _field_mentioned(v, rule)]
        if hits:
            matched.append((name, hits))
    if len(matched) == 1:
        name, hits = matched[0]
        return {"when_field": name, "when_values": hits}
    return {}


def _closed_field_pair(raw: Any) -> list[str]:
    if not isinstance(raw, list) or len(raw) != 2:
        return []
    pair = [str(raw[0]).strip(), str(raw[1]).strip()]
    if all(pair) and pair[0] != pair[1]:
        return pair
    return []


def _checkable_params(
    ctype: str, rule: str, raw: Any, *, schema: dict[str, str] | None = None
) -> dict[str, Any]:
    params = dict(raw) if isinstance(raw, dict) else {}
    pair = _closed_field_pair(params.get("when_fields_differ"))
    if pair:
        params["when_fields_differ"] = pair
    else:
        params.pop("when_fields_differ", None)
    if ctype == "output_format" and not params.get("format") and re.search(r"\bjson\b", rule, re.I):
        params["format"] = "json"
    if ctype in ("ordering", "precondition") and not params.get("position"):
        found = _POSITION_RE.search(rule)
        if found:
            params["position"] = found.group(1).lower()
    if (
        ctype in ("ordering", "precondition")
        and schema
        and not params.get("when_field")
        and not params.get("when_fields_differ")
        and not params.get("position")
        and _SITUATIONAL_RE.search(rule)
    ):
        params.update(situational_trigger_params(rule, schema))
    return params


def _arg_schema_from_tool(tool: dict[str, Any]) -> list[dict[str, Any]]:
    """Required keys and canonical kinds only. A bare ``string`` is presence, not a type."""
    raw = tool.get("arguments")
    if not isinstance(raw, list):
        return []
    out: list[dict[str, Any]] = []
    for arg in raw:
        if not isinstance(arg, dict):
            continue
        name = str(arg.get("name") or "").strip()
        if not name:
            continue
        type_text = _declaration_text(arg.get("type"))
        desc = str(arg.get("description") or "").strip()
        declaration = f"{type_text} — {desc}" if type_text and desc else (type_text or desc)
        spec: dict[str, Any] = {
            "name": name,
            "required": bool(arg.get("required")),
            "nullable": bool(re.search(r"\bnull\b", type_text, re.I)),
        }
        kind = canonical_field_kind(name, declaration)
        if kind and kind != "string":
            spec["kind"] = kind
            if kind == "enum":
                spec["enum"] = _declared_enum_values(declaration)
        out.append(spec)
    return out


def _tool_argument_entries(card: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for tool in card.get("tool_spec") or []:
        if not isinstance(tool, dict):
            continue
        name = str(tool.get("name") or "").strip()
        schema = _arg_schema_from_tool(tool)
        if not name or not schema:
            continue
        rule = f"{name} arguments match the declared schema"
        entries.append(
            {
                "id": stable_ref_id(rule),
                "rule": rule,
                "type": "tool_arguments",
                "params": {"tool": name, "arguments": schema},
                "tools": [name],
            }
        )
    return entries


def constraint_entries(card: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The same dicts ``card-constraints`` scores, including per-tool argument schemas."""
    if not card:
        return []
    schema = schema_field_map(card)
    entries: list[dict[str, Any]] = []
    for item in card.get("constraints") or []:
        if not isinstance(item, dict):
            continue
        rule = str(item.get("rule") or "").strip()
        if not rule:
            continue
        ctype = str(item.get("type") or "")
        entries.append(
            {
                "id": stable_ref_id(rule),
                "rule": rule,
                "type": ctype,
                "params": _checkable_params(ctype, rule, item.get("params"), schema=schema),
                "tools": [],
            }
        )
    for item in card.get("tool_protocol") or []:
        if not isinstance(item, dict):
            continue
        rule = str(item.get("rule") or "").strip()
        if not rule:
            continue
        ctype = str(item.get("kind") or "")
        entries.append(
            {
                "id": stable_ref_id(rule),
                "rule": rule,
                "type": ctype,
                "params": _checkable_params(ctype, rule, item.get("params"), schema=schema),
                "tools": [str(t) for t in item.get("tools") or [] if str(t).strip()],
            }
        )
    entries.extend(_tool_argument_entries(card))
    return entries


def _output_only_constraint(entry: dict[str, Any]) -> bool:
    if str(entry.get("type") or "") != "output_format":
        return False
    params = entry.get("params") if isinstance(entry.get("params"), dict) else {}
    if params.get("fence_output") is False:
        return True
    return str(params.get("format") or "").lower() == "json"


def _compile_card_constraints(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    """Each entry id is ``stable_ref_id(rule)`` so a rescore addresses the
    same compiled check."""
    card = grounding.codebase_card or {}
    entries = constraint_entries(card)
    if not entries:
        return []
    declared = [
        str(t.get("name")).strip()
        for t in card.get("tool_spec") or []
        if isinstance(t, dict) and str(t.get("name") or "").strip()
    ]
    blocked: list[str] = []
    if (
        not any(_output_only_constraint(e) for e in entries)
        and _stored_tool_call_total(grounding) <= 0
    ):
        blocked = ["generate-mode variants (stored rows contain no structured tool calls)"]
    return [
        EvaluatorSpec(
            name="card-constraints",
            description=(
                "Mechanically checks the capability card's behavioral constraints and "
                "tool-protocol rules against the trace (call order, budgets, tool "
                "discipline, output format), abstaining on rules it cannot verify."
            ),
            kind="deterministic",
            scope="trajectory",
            score_type="numeric",
            config={
                "check": "card_constraints",
                "constraints": entries,
                "declared_tools": declared,
            },
            provenance=_provenance(
                grounding,
                source="codebase_card.constraints",
                surface_area="tool_surface",
                blocked_on=blocked,
            ),
        )
    ]


def _anatomy_from_blob(blob: Any) -> dict[str, Any]:
    if not isinstance(blob, dict):
        return {}
    anatomy = blob.get("conversation_anatomy")
    if isinstance(anatomy, dict) and anatomy:
        return anatomy
    profile = blob.get("profile")
    if isinstance(profile, dict):
        nested = profile.get("conversation_anatomy")
        if isinstance(nested, dict) and nested:
            return nested
    card = blob.get("card")
    if isinstance(card, dict) and card is not blob:
        return _anatomy_from_blob(card)
    return {}


def _conversation_anatomy(grounding: EvalGroundingContext) -> dict[str, Any]:
    for blob in (grounding.dataset_card, grounding.report):
        anatomy = _anatomy_from_blob(blob)
        if anatomy:
            return anatomy
    return {}


def _tool_total_from_anatomy(anatomy: dict[str, Any]) -> int:
    tool_calls = anatomy.get("tool_calls")
    total = tool_calls.get("total") if isinstance(tool_calls, dict) else tool_calls
    try:
        return int(total or 0)
    except (TypeError, ValueError):
        return 0


def _stored_tool_call_total(grounding: EvalGroundingContext) -> int:
    return _tool_total_from_anatomy(_conversation_anatomy(grounding))


def _rows_observe_tools(dataset) -> bool:
    if dataset is None:
        return False
    from overbae.services.datasets.rows import dataset_stats, sample_rows

    if dataset_stats(dataset).get("has_tool_calling"):
        return True
    points = sample_rows(dataset, 8)
    for point in points:
        extra = point.extra or {}
        if extra.get("tool_calls") or extra.get("tool_graph"):
            return True
        try:
            if int(extra.get("num_tool_calls") or 0) > 0:
                return True
        except (TypeError, ValueError):
            continue
    return False


def generate_observes_tool_calls(grounding: EvalGroundingContext) -> bool:
    """Generate-mode replay emits structured tool calls only when the stored
    rows did. The same signal stamps ``blocked_on`` on trajectory deterministics."""
    if _stored_tool_call_total(grounding) > 0:
        return True
    return _rows_observe_tools(grounding.dataset)


def _observed_tool_names(grounding: EvalGroundingContext) -> list[str]:
    """The deterministic record of the tool surface the dataset's traces
    actually exercised. Empty when none was recorded, so callers must not gate
    on an absent signal."""
    tool_calls = _conversation_anatomy(grounding).get("tool_calls")
    if not isinstance(tool_calls, dict):
        return []
    return [str(t).strip() for t in tool_calls.get("unique_tools") or [] if str(t).strip()]


def _compile_tool_surface(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    card = grounding.codebase_card
    if not card:
        return []
    tools = [
        str(t.get("name")).strip()
        for t in card.get("tool_spec") or []
        if isinstance(t, dict) and str(t.get("name") or "").strip()
    ]
    if not tools:
        return []
    blocked = []
    if _stored_tool_call_total(grounding) <= 0:
        blocked = ["generate-mode variants (stored rows contain no structured tool calls)"]
    # A declared vocabulary that does not intersect the observed tool surface
    # would score 0 or abstain on every sample, so mark it not-relevant while
    # keeping it selectable. Only fires when a surface was actually observed.
    observed = _observed_tool_names(grounding)
    if observed and not (set(observed) & set(tools)):
        blocked.append(
            "observed tool surface "
            f"({', '.join(sorted(observed)[:8])}) does not intersect the declared tool "
            f"vocabulary ({', '.join(sorted(tools)[:8])})"
        )
    return [
        EvaluatorSpec(
            name="tool-vocabulary-selection",
            description=(
                "Precision of called tools against the capability's declared vocabulary. "
                "Skipping an optional declared tool is not a miss; calling an undeclared one is."
            ),
            kind="deterministic",
            scope="trajectory",
            score_type="numeric",
            config={"check": "tool_selection", "expected_tools": tools},
            provenance=_provenance(
                grounding,
                source="codebase_card.tool_spec",
                surface_area="tool_surface",
                blocked_on=blocked,
            ),
        )
    ]


_PROSE_TYPE_TOKENS = {
    "string": "string",
    "str": "string",
    "text": "string",
    "number": "number",
    "numeric": "number",
    "float": "number",
    "decimal": "number",
    "double": "number",
    "int": "integer",
    "integer": "integer",
    "bool": "boolean",
    "boolean": "boolean",
    "array": "array",
    "list": "array",
    "object": "object",
    "dict": "object",
    "map": "object",
}

_NUM = r"(-?\d+(?:\.\d+)?)"


def _prose_leading_type(desc: str) -> tuple[str | None, bool]:
    head = re.match(r"\s*([A-Za-z_|]+)", desc or "")
    if not head:
        return None, False
    tokens = [t.strip().lower() for t in head.group(1).split("|") if t.strip()]
    nullable = "null" in tokens or "none" in tokens
    types = [_PROSE_TYPE_TOKENS.get(t) for t in tokens if t not in ("null", "none")]
    if len(types) == 1 and types[0]:
        return types[0], nullable
    return None, nullable


def _prose_enum(desc: str) -> list[str] | None:
    match = re.search(r"\benum\s+([A-Za-z0-9_|\-]+)", desc)
    if match and "|" in match.group(1):
        return [t for t in match.group(1).split("|") if t]
    match = re.search(r"one of[:\s]+([^.;\n(]+)", desc, re.IGNORECASE)
    if match:
        values = [v.strip().strip("'\"`") for v in re.split(r",|\bor\b|\|", match.group(1))]
        values = [v for v in values if v]
        if len(values) >= 2:
            return values
    return None


def _prose_range(desc: str) -> tuple[float | None, float | None]:
    match = re.search(rf"in\s*\[\s*{_NUM}\s*,\s*{_NUM}\s*\]", desc)
    if match:
        return float(match.group(1)), float(match.group(2))
    lo = hi = None
    match = re.search(rf"\bge\s*=\s*{_NUM}", desc)
    if match:
        lo = float(match.group(1))
    match = re.search(rf"\ble\s*=\s*{_NUM}", desc)
    if match:
        hi = float(match.group(1))
    if lo is None and hi is None:
        match = re.search(rf"between\s+{_NUM}\s+and\s+{_NUM}", desc, re.IGNORECASE)
        if match:
            lo, hi = float(match.group(1)), float(match.group(2))
    return lo, hi


def _prose_field_rules(desc: str) -> dict[str, Any]:
    text = str(desc or "")
    rules: dict[str, Any] = {}
    if not text.strip():
        return rules
    field_type, nullable = _prose_leading_type(text)
    if field_type:
        rules["type"] = field_type
    if nullable or re.search(r"\bnull\b", text, re.IGNORECASE):
        rules["nullable"] = True
    enum = _prose_enum(text)
    if enum:
        rules["enum"] = enum
        rules.setdefault("type", "string")
    lo, hi = _prose_range(text)
    if lo is not None:
        rules["min"] = lo
    if hi is not None:
        rules["max"] = hi
    if rules.get("type") in (None, "string"):
        if "YYYY-MM-DD" in text:
            rules["pattern"] = r"^\d{4}-\d{2}-\d{2}$"
        elif re.search(r"\bISO[- ]?8601\b|\bISO timestamp\b", text, re.IGNORECASE):
            rules["pattern"] = r"^\d{4}-\d{2}-\d{2}[T ]"
    return rules


_FORMAT_PATTERNS = {
    "date": r"^\d{4}-\d{2}-\d{2}$",
    "date-time": r"^\d{4}-\d{2}-\d{2}[T ]",
}


def _schema_field_rules(meta: Any) -> dict[str, Any]:
    """Explicit schema keys win over the entry's prose."""
    if isinstance(meta, str):
        return _prose_field_rules(meta)
    if not isinstance(meta, dict):
        return {}
    rules = _prose_field_rules(str(meta.get("description") or ""))
    declared_type = meta.get("type")
    if isinstance(declared_type, str) and declared_type in _PROSE_TYPE_TOKENS:
        rules["type"] = _PROSE_TYPE_TOKENS[declared_type]
    if isinstance(meta.get("enum"), list) and meta["enum"]:
        rules["enum"] = [str(e) for e in meta["enum"]]
    if isinstance(meta.get("minimum"), (int, float)):
        rules["min"] = meta["minimum"]
    if isinstance(meta.get("maximum"), (int, float)):
        rules["max"] = meta["maximum"]
    if isinstance(meta.get("pattern"), str) and meta["pattern"]:
        rules["pattern"] = meta["pattern"]
    if meta.get("nullable") is True:
        rules["nullable"] = True
    fmt = str(meta.get("format") or "").strip()
    if fmt in _FORMAT_PATTERNS and "pattern" not in rules:
        rules["pattern"] = _FORMAT_PATTERNS[fmt]
    # Cards may declare {"tolerance": {"abs": 0.01}}, {"tolerance": {"rel": 0.05}} or a bare number.
    tolerance = meta.get("tolerance")
    if isinstance(tolerance, dict):
        tol_rules = {
            k: float(v) for k, v in tolerance.items() if k in ("abs", "rel") and _is_num(v)
        }
        if tol_rules:
            rules["tolerance"] = tol_rules
    elif _is_num(tolerance):
        rules["tolerance"] = {"abs": float(tolerance)}
    return rules


def _is_num(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _card_success_criteria_texts(card: dict[str, Any] | None) -> list[str]:
    texts: list[str] = []
    for item in (card or {}).get("success_criteria") or []:
        if isinstance(item, str):
            texts.append(item.strip())
        elif isinstance(item, dict):
            texts.append(str(item.get("text") or item.get("criterion") or "").strip())
    return [text for text in texts if text]


def card_warrants_label_accuracy(card: dict[str, Any] | None) -> bool:
    """Card-only signal for minting a reference comparator before a dataset exists."""
    if not card or card.get("_fallback"):
        return False
    from overbae.services.eval.surface_binding import is_gold_comparator_claim

    if any(is_gold_comparator_claim(text) for text in _card_success_criteria_texts(card)):
        return True
    vocab = card.get("vocabulary") or {}
    has_vocab = isinstance(vocab, dict) and len(vocab) >= 2
    if not has_vocab:
        return False
    if _output_contract_kind(card) == "scalar_string":
        return True
    return _construct_label_from_prose(card, "") == "classification"


def _compile_label_accuracy(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    """Deterministic gold comparator when the card or dataset warrants reference grading."""
    from overbae.services.eval.profiler import dataset_has_closed_form_reference, profile_dataset

    card = grounding.codebase_card or {}
    dataset = grounding.dataset
    has_dataset = dataset is not None and getattr(dataset, "pk", None) is not None
    closed_form = has_dataset and dataset_has_closed_form_reference(dataset)
    card_gold = card_warrants_label_accuracy(card)
    if not closed_form and not card_gold:
        return []

    profile = profile_dataset(dataset) if closed_form else {}
    schema = card.get("output_schema") or {}
    required = [str(k) for k in schema.get("required_keys") or [] if str(k).strip()]
    structured = bool(
        required and closed_form and profile.get("output_kind") not in ("label", None)
    )
    if structured:
        properties = schema.get("properties") or {}
        if not isinstance(properties, dict):
            return []
        fields = {name: _schema_field_rules(properties.get(name)) for name in required}
        if not fields:
            return []
        return [
            EvaluatorSpec(
                name="label-accuracy",
                display_name="Required fields match reference",
                description=(
                    "Each card-required output field equals the reference row's value "
                    "(structured tolerances honoured for numeric fields)."
                ),
                kind="deterministic",
                scope="final_output",
                score_type="numeric",
                requires_reference=True,
                config={"check": "reference_field_compare", "fields": fields},
                provenance=_provenance(
                    grounding,
                    source="codebase_card.output_schema.required_keys",
                    surface_area="reference",
                ),
            )
        ]

    source = "dataset.closed_form_reference" if closed_form else "codebase_card.success_criteria"
    return [
        EvaluatorSpec(
            name="label-accuracy",
            display_name="Exact Match",
            description="Output exactly equals the reference (case-insensitive).",
            kind="deterministic",
            scope="final_output",
            score_type="boolean",
            requires_reference=True,
            config={"check": "exact_match", "case_insensitive": True},
            provenance=_provenance(grounding, source=source, surface_area="reference"),
        )
    ]


def _compile_reference_field_compare(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    """REQUIRED fields only; optional fields are the LLM compare judges' job."""
    card = grounding.codebase_card
    if not card:
        return []
    reference_cols = [
        col
        for col, role in ((grounding.dataset_card or {}).get("io_mapping") or {}).items()
        if role == "reference"
    ]
    if not reference_cols:
        return []
    schema = card.get("output_schema") or {}
    required = [str(k) for k in schema.get("required_keys") or [] if str(k).strip()]
    if not required:
        return []
    properties = schema.get("properties") or {}
    fields = {
        name: _schema_field_rules(properties.get(name))
        for name in required
        if isinstance(properties, dict)
    }
    return [
        EvaluatorSpec(
            name="reference-required-field-compare",
            display_name="Required fields match reference",
            description=(
                "Each card-required output field equals the reference row's value "
                "(structured tolerances honoured for numeric fields)."
            ),
            kind="deterministic",
            scope="final_output",
            score_type="numeric",
            requires_reference=True,
            config={"check": "reference_field_compare", "fields": fields},
            provenance=_provenance(
                grounding,
                source="codebase_card.output_schema.required_keys",
                surface_area="reference",
            ),
        )
    ]


def _trajectory_paths(card: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [
        p for p in (card or {}).get("trajectory_map") or [] if isinstance(p, dict) and p.get("id")
    ]


def judged_backbone_steps(path: dict[str, Any]) -> list[dict[str, Any]]:
    """Identity is the card's structured ``steps``, never anchor pairs or one
    eval per tool. Outside ``code_path`` claims, a bare label with no code
    attachment is not judged as a step."""
    claim = str(path.get("claim") or "code_path")
    steps = [
        s
        for s in path.get("steps") or []
        if isinstance(s, dict) and str(s.get("step") or "").strip()
    ]
    if not steps:
        return []
    if claim == "code_path":
        return steps
    return [
        s
        for s in steps
        if s.get("kind") == "model_invocation" or s.get("anchors") or s.get("may_use")
    ]


def compile_behaviour_suites(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    """One outcome judge plus one linked step judge per behaviour. Binding
    (``config["behaviour"]``) selects members; it does not score."""
    specs: list[EvaluatorSpec] = []
    seen: set[str] = set()
    for path in _trajectory_paths(grounding.codebase_card):
        key = str(path["id"])
        outcome = _behaviour_success_judge(grounding, path, key)
        if outcome.name not in seen:
            seen.add(outcome.name)
            specs.append(outcome)
        for step in judged_backbone_steps(path):
            spec = _behaviour_step_judge(grounding, path, key, step)
            if spec.name in seen:
                continue
            seen.add(spec.name)
            specs.append(spec)
    return specs


def _behaviour_step_judge(
    grounding: EvalGroundingContext, path: dict[str, Any], key: str, step: dict[str, Any]
) -> EvaluatorSpec:
    label = str(step.get("step") or "").strip()
    slug = _slug(label)
    kind = str(step.get("kind") or "agent_step")
    segment = [str(a) for a in step.get("anchors") or [] if str(a or "").strip()]
    routing = str(path.get("routing") or "").strip()
    grounding_lines: list[str] = []
    if kind == "model_invocation":
        for field, heading in (
            ("input", "Model input"),
            ("action", "Model action"),
            ("output", "Model output"),
        ):
            text = str(step.get(field) or "").strip()
            if text:
                grounding_lines.append(f"{heading}: {text}")
    rubric = (
        f'Judge ONE step of behaviour "{key}"' + (f" — {routing}" if routing else "") + ".\n"
        f'The step under judgment is "{label}" ({kind}). The trajectory variable '
        "carries every prior action in order: everything before this step is the "
        "running context it must be judged against. Judge ONLY this step — do not "
        "re-grade the overall outcome, and do not penalize the step for evidence "
        "that simply isn't part of it. An explicit empty/reject output is the "
        "agent's DECISION, not a failed execution: when rejecting is the right "
        "call for the input and intent, the step that produced the rejection was "
        "performed well. The capability map and tool list are what the agent can "
        "do, never a required call list. A poor choice inside a warranted step "
        "is a quality miss. Fail warranted only when this step is extra "
        "off-intent work that should not have happened, not because it skipped "
        "a mapped tool or route. A turn that ends in a first clarifying "
        "question or a first confirm-park before a gated write is a "
        "legitimate terminal: steps that correctly pause there serve the "
        "intent and must not be failed for not advancing further. A span "
        "that records no output payload is an INSTRUMENTATION GAP, not "
        "failure evidence: when the span completed without error and the "
        "only complaint would be that its output was not recorded, grade "
        "the step on what IS observable and do not fail it for the gap."
    )
    if grounding_lines:
        rubric += "\nWhat this step is (evidence, not a required action):\n" + "\n".join(
            grounding_lines
        )
    return EvaluatorSpec(
        name=f"behaviour-{_slug(key)}-step-{slug}",
        display_name=f"Step: {_title(label)}",
        description=(
            f'Linked step judge for behaviour "{key}": {label}, '
            "conditioned on the trajectory so far."
        ),
        kind="llm_judge",
        applicable_roles=["trace_scoring"],
        # Step scope would slice evidence per turn and score turns whose output
        # lands later as "produced no result".
        scope="trajectory",
        score_type="numeric",
        rubric_md=rubric,
        checklist=[
            {
                "id": f"step-{slug}-warranted",
                "q": (
                    "Given the user input, the running conversation intent, prior "
                    "turns of this conversation, and the steps taken BEFORE this "
                    "one in the trajectory, should this step have happened here? "
                    "Normal backbone work for a turn that should run (assemble "
                    "context, decide, act, answer) is warranted even when the "
                    "eventual choice was poor — that is a quality miss, not "
                    "unwarranted. Fail when this step includes extra off-intent "
                    "activity that the ask did not need, even if it also did "
                    "in-scope work. Do not fail it for skipping a mapped tool, "
                    "terminal, or route. A new user instruction supersedes the "
                    "prior running intent: work serving THIS turn's ask is "
                    "on-intent even when an earlier ask went unmet, was "
                    "correctly refused, or was abandoned by the user — do not "
                    "fail the step for not continuing a superseded or "
                    "correctly refused ask."
                ),
                # This verdict becomes ``passed``; the score stays graded.
                "gate": True,
            },
            {
                "id": f"step-{slug}-quality",
                "q": (
                    "Was the step performed well — correct, faithful to the running "
                    "context, and advancing the task toward the intent? Pausing on "
                    "a first clarifying question or a first confirm-park before a "
                    "gated write IS advancing the task when the ask genuinely "
                    "needs it."
                ),
            },
        ],
        variable_mapping=[
            {"var": "input", "source": "input"},
            {"var": "trajectory", "source": "trajectory"},
            {"var": "output", "source": "output"},
        ],
        config={"behaviour": {"behaviour_key": key, "role": "step", "anchor_segment": segment}},
        provenance=_provenance(
            grounding,
            source=f"codebase_card.trajectory_map[{key}].steps[{slug}]",
            surface_area="trajectory",
        ),
    )


def _behaviour_success_judge(
    grounding: EvalGroundingContext, path: dict[str, Any], key: str
) -> EvaluatorSpec:
    routing = str(path.get("routing") or "").strip()
    claim = str(path.get("claim") or "code_path")
    quote = str(path.get("prompt_quote") or "").strip()
    context: list[str] = [f'Behaviour "{key}" ({claim})' + (f" — {routing}" if routing else "")]
    if quote:
        context.append(f"Declared task: {quote}")
    rubric = (
        "Judge THIS UNIT only: did it serve its task? When the grounding "
        "context carries an attested user ask, that ask IS the task. When it "
        "does not (no ask, or only unattested scraped input context), the "
        "task is the declared behaviour below — judge service of THAT, and "
        "never grade this unit against asks that belong to other "
        "capabilities. The whole-run timeline is context, not a second "
        "score.\n"
        + "\n".join(context)
        + "\nThe behaviour context above is EVIDENCE for what the task is, never a "
        "pass/fail gate: an unusual route that correctly serves the user's need still "
        "succeeds, and a usual route that fails the user's need still fails. A "
        "correct refusal of an ask that does not warrant this task is a SUCCESS. "
        "Do not grade reply format, presentation, citation hygiene, or schema "
        "shape — those are not this evaluation. Do not require any particular "
        "tool, terminal, or mapped step. A span that records no output "
        "payload is an INSTRUMENTATION GAP, not failure evidence: a unit "
        "whose spans completed without error must not fail merely because "
        "outputs were not recorded. The overall score is this one concern: "
        "a pass is high, a fail is low. Naming a miss in the rationale while "
        "still passing is a grading error: the gate below is the score."
    )
    return EvaluatorSpec(
        name=f"behaviour-{_slug(key)}-success",
        display_name=f"Task outcome: {_title(key)}",
        description=(
            f'Task-success judge for behaviour "{key}": given the whole-run '
            "context and this unit's actions, did the unit serve the ask?"
        ),
        kind="llm_judge",
        applicable_roles=["trace_scoring"],
        scope="trajectory",
        score_type="numeric",
        rubric_md=rubric,
        checklist=[
            {
                "id": "serves-open-ask",
                "gate": True,
                "q": (
                    "Did this unit serve its task — the attested user ask when "
                    "the grounding context carries one, otherwise the declared "
                    "behaviour? Grade from the span tree (every span's "
                    "unwrapped I/O) — an MCP/Cursor envelope is not an empty "
                    "sample if inner rows or errors are present. Interpret what "
                    "was asked — do not require any particular tool, "
                    "route, or terminal."
                ),
            },
            {
                "id": "delivered-kind-matches",
                "q": (
                    "Does the delivered result match the ask's kind, type, and "
                    "intent? Compare declared identity fields on produced "
                    "objects (intent, kind, purpose in span I/O) to the ask's "
                    "constraints. A produced artifact of the wrong kind, type, "
                    "or intent is a fail — not a partial pass, and not a pass "
                    "because the agent reported the mismatch; reporting a "
                    "wrong-kind result is failed delivery, not a real blocker."
                ),
            },
            {
                "id": "miss-is-legitimate",
                "q": (
                    "If this turn delivered nothing, is the miss genuine "
                    "ambiguity or inability — missing required input the user "
                    "never gave, a real blocker (tool error, auth, gone "
                    "resource) the agent surfaces, a first clarifying question, "
                    "or the first parking to confirm a gated write? Listing, "
                    "explaining, or re-parking an ask the ledger shows already "
                    "parked or re-prompted, without a new real blocker, is "
                    "obstruction and a fail."
                ),
            },
            {
                "id": "evidence-supported-reply",
                "q": (
                    "Is the reply supported by the evidence? Empty, null, or "
                    "truncated tool results are not a miss when the ask was to "
                    "look something up, but a reply asserting details those "
                    "payloads do not contain is. A correct refusal of an "
                    "out-of-scope ask is a pass."
                ),
            },
        ],
        variable_mapping=[
            {"var": "input", "source": "input"},
            {"var": "trajectory", "source": "trajectory"},
            {"var": "tool_calls", "source": "tool_calls"},
            {"var": "output", "source": "output"},
        ],
        config={"behaviour": {"behaviour_key": key, "role": "outcome", "anchor_segment": []}},
        provenance=_provenance(
            grounding,
            source=f"codebase_card.trajectory_map[{key}].success",
            surface_area="trajectory",
        ),
    )


def _compile_spine_constants(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    card = grounding.dataset_card
    if not card:
        return []
    output_fields = (grounding.codebase_card or {}).get("output_fields") or {}
    if "summary" not in output_fields:
        return []
    try:
        total_rows = int((card.get("volume_and_tokens") or {}).get("total_rows") or 0)
    except (TypeError, ValueError):
        total_rows = 0
    if total_rows <= 0:
        return []
    return [
        EvaluatorSpec(
            name="summary-rows-authoritative",
            display_name="Summary row count is authoritative",
            description=(
                f"The report's summary.rows must cite the authoritative row count "
                f"({total_rows}) for this dataset version, not an invented or stale figure."
            ),
            kind="deterministic",
            scope="final_output",
            score_type="boolean",
            config={"check": "regex", "pattern": rf'"rows"\s*:\s*{total_rows}\b'},
            # Only the real harness produces the summary block.
            evidence_requirement="harness_artifact",
            provenance=_provenance(
                grounding,
                source="dataset_card.volume_and_tokens.total_rows",
                surface_area="output_contract",
            ),
        )
    ]


def _compile_reference_rollups(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    card = grounding.dataset_card
    if not card:
        return []
    reference_cols = [
        col for col, role in (card.get("io_mapping") or {}).items() if role == "reference"
    ]
    if not reference_cols:
        return []
    compatibility = (grounding.report or {}).get("compatibility") or {}
    blocked: list[str] = []
    if compatibility.get("eval_ready") is False:
        blocked = [str(m) for m in compatibility.get("missing_for_eval") or []] or [
            "dataset is not eval_ready per the workshop report"
        ]
    cols = ", ".join(reference_cols)
    specs = []
    for metric, label in (("rouge_l", "ROUGE-L"), ("embedding_cosine", "embedding cosine")):
        specs.append(
            EvaluatorSpec(
                name=f"reference-similarity-{metric.replace('_', '-')}",
                display_name=f"Reference similarity ({label})",
                description=f"Corpus-level {label} of outputs vs the reference column(s) {cols}.",
                kind="statistical",
                scope="dataset",
                score_type="numeric",
                requires_reference=True,
                config={"metric": metric},
                provenance=_provenance(
                    grounding,
                    # Distinct dedup key per rollup.
                    source=f"dataset_card.io_mapping[{metric}]",
                    surface_area="reference",
                    blocked_on=blocked,
                ),
            )
        )
    return specs


def _compile_format_gate(grounding: EvalGroundingContext) -> list[EvaluatorSpec]:
    compliance = (grounding.report or {}).get("format_compliance") or {}
    if str(compliance.get("expected_format") or "").lower() != "json":
        return []
    baseline = compliance.get("valid_pct")
    return [
        EvaluatorSpec(
            name="output-parses-as-json",
            display_name="Output parses as JSON",
            description="Bare JSON parse gate (the report expects JSON-formatted outputs).",
            kind="deterministic",
            scope="final_output",
            score_type="boolean",
            config={"check": "json_schema_valid"},
            provenance=_provenance(
                grounding,
                source="report.format_compliance",
                surface_area="output_contract",
                baseline=float(baseline) if isinstance(baseline, (int, float)) else None,
            ),
        )
    ]


def _compile_bare_parse_gate(
    grounding: EvalGroundingContext, existing: list[EvaluatorSpec]
) -> list[EvaluatorSpec]:
    """The envelope must parse; its fields are not contractual."""
    card = grounding.codebase_card
    if not card or _output_contract_kind(card) != "json_object":
        return []
    if any(spec.name == "output-parses-as-json" for spec in existing):
        return []
    return [
        EvaluatorSpec(
            name="output-parses-as-json",
            display_name="Output parses as JSON",
            description=(
                "Bare JSON parse gate for the agent's output envelope — no per-field "
                "contract (open/analytical construct)."
            ),
            kind="deterministic",
            scope="final_output",
            score_type="boolean",
            config={"check": "json_schema_valid"},
            provenance=_provenance(
                grounding,
                source="codebase_card.output_contract.parse",
                surface_area="output_contract",
            ),
        )
    ]


def _inventory_keys(inventory: list[Any]) -> set[tuple[str, str]]:
    keys: set[tuple[str, str]] = set()
    for evaluator in inventory or []:
        provenance = (getattr(evaluator, "config", None) or {}).get("provenance") or {}
        source = str(provenance.get("source") or "")
        if source:
            keys.add((source, str(provenance.get("data_version") or "")))
    return keys


def _dedup(specs: list[EvaluatorSpec], inventory: list[Any]) -> list[EvaluatorSpec]:
    existing = _inventory_keys(inventory)
    seen: set[tuple[str, str]] = set()
    out: list[EvaluatorSpec] = []
    for spec in specs:
        key = spec.provenance_key()
        if key in existing or key in seen:
            continue
        seen.add(key)
        out.append(spec)
    return out


def _coverage_cells(grounding: EvalGroundingContext) -> dict[str, list[str]]:
    dataset_card = grounding.dataset_card or {}
    codebase_card = grounding.codebase_card or {}
    report = grounding.report or {}

    failure_cells = [
        f"dataset: {str(fm.get('description') or '')[:80]}"
        for fm in dataset_card.get("failure_modes") or []
        if isinstance(fm, dict) and fm.get("description")
    ]
    failure_cells += [
        f"codebase: {str(fm)[:80]}" for fm in codebase_card.get("failure_modes") or [] if str(fm)
    ]
    cohort_cells = [
        str(issue.get("id") or issue.get("fix") or "")
        for issue in (report.get("eval_readiness") or {}).get("issues") or []
        if isinstance(issue, dict) and (issue.get("id") or issue.get("fix"))
    ]
    return {
        "output_contract": [str(k) for k in codebase_card.get("output_fields") or {}],
        "failure_mode": failure_cells,
        "tool_surface": [
            str(t.get("name"))
            for t in codebase_card.get("tool_spec") or []
            if isinstance(t, dict) and t.get("name")
        ],
        "cohort": cohort_cells,
        "reference": [
            col
            for col, role in (dataset_card.get("io_mapping") or {}).items()
            if role == "reference"
        ],
        "trajectory": [str(p["id"]) for p in _trajectory_paths(codebase_card)],
    }


def _covered_cells(area: str, cells: list[str], specs: list[EvaluatorSpec]) -> list[str]:
    area_specs = [s for s in specs if s.provenance.surface_area == area]
    if not area_specs:
        return []
    if area == "output_contract":
        keys: set[str] = set()
        for spec in area_specs:
            keys.update(str(k) for k in spec.config.get("required_keys") or [])
        return [c for c in cells if c in keys]
    if area == "failure_mode":
        covered_indices: set[int] = set()
        for spec in area_specs:
            match = re.match(r"dataset_card\.failure_modes\[(\d+)\]", spec.provenance.source)
            if match:
                covered_indices.add(int(match.group(1)))
        dataset_modes = [c for c in cells if c.startswith("dataset: ")]
        covered = {dataset_modes[i] for i in covered_indices if i < len(dataset_modes)}
        cited_text = " ".join(s.description + " " + s.provenance.source for s in area_specs)
        for cell in cells:
            label = cell.split(": ", 1)[-1][:40].lower()
            if label and label in cited_text.lower():
                covered.add(cell)
        return [c for c in cells if c in covered]
    if area == "tool_surface":
        expected: set[str] = set()
        for spec in area_specs:
            expected.update(str(t) for t in spec.config.get("expected_tools") or [])
        return [c for c in cells if c in expected]
    if area == "trajectory":
        # Path ids live verbatim in judge rubrics, not only in provenance sources.
        cited = " ".join(
            f"{s.provenance.source} {s.description} {s.rubric_md}" for s in area_specs
        ).lower()
        return [c for c in cells if c.lower() in cited]
    cited = " ".join(s.provenance.source + " " + s.description for s in area_specs).lower()
    return [c for c in cells if c.lower() in cited]


def compute_coverage(grounding: EvalGroundingContext, specs: list[EvaluatorSpec]) -> dict[str, Any]:
    cells_by_area = _coverage_cells(grounding)
    areas: list[dict[str, Any]] = []
    total_cells = 0
    total_covered = 0
    for area, cells in cells_by_area.items():
        covered = _covered_cells(area, cells, specs)
        uncovered = [c for c in cells if c not in covered]
        areas.append({"area": area, "cells": cells, "covered": covered, "uncovered": uncovered})
        total_cells += len(cells)
        total_covered += len(covered)
    score = round(total_covered / total_cells, 4) if total_cells else 0.0
    return {"areas": areas, "score": score}


_ALLOCATION_SUITES: tuple[str, ...] = ("generative", "trace_scoring")
_CELL_LABEL_LEN = 160
_CLUSTER_LABEL_LEN = 600


@dataclass
class SignalCell:
    """Key doubles as the grounding citation; a cluster cell holds a whole
    family that gets exactly ONE judge per suite."""

    area: str
    key: str
    label: str
    suites: tuple[str, ...] = _ALLOCATION_SUITES
    tier0_check: str = ""
    note: str = ""


def _cluster_label(heading: str, items: list[str]) -> str:
    body = "; ".join(f"[{i}] {str(item)[:120]}" for i, item in enumerate(items))
    return f"{heading} — one judge covering: {body}"[:_CLUSTER_LABEL_LEN]


def _observable_cluster_cell(
    area: str,
    key: str,
    heading: str,
    items: list[str],
    card: dict[str, Any],
    observes_tools: bool,
    tool_suites: tuple[str, ...],
) -> SignalCell:
    observable, harness = split_generate_observability(
        card, items, generate_observes_tools=observes_tools
    )
    if observable:
        return SignalCell(area, key, _cluster_label(heading, observable))
    return SignalCell(area, key, _cluster_label(heading, harness), suites=tool_suites)


def signal_cells(grounding: EvalGroundingContext, construct: dict[str, str]) -> list[SignalCell]:
    codebase = grounding.codebase_card or {}
    dataset = grounding.dataset_card or {}
    report = grounding.report or {}
    cells: list[SignalCell] = []
    observes_tools = generate_observes_tool_calls(grounding)
    tool_suites = _ALLOCATION_SUITES if observes_tools else ("trace_scoring",)

    criteria = [str(c) for c in codebase.get("success_criteria") or [] if str(c).strip()]
    if criteria:
        cells.append(
            _observable_cluster_cell(
                "output_contract",
                "codebase_card.success_criteria",
                "task success / intended-behaviour cluster",
                criteria,
                codebase,
                observes_tools,
                tool_suites,
            )
        )
    expected = codebase.get("expected_output") if isinstance(codebase, dict) else None
    expected = expected if isinstance(expected, dict) else {}
    quality = [str(s) for s in expected.get("quality_signals") or [] if str(s).strip()]
    if quality:
        cells.append(
            _observable_cluster_cell(
                "output_contract",
                "codebase_card.expected_output.quality_signals",
                "expected-output quality-signal cluster",
                quality,
                codebase,
                observes_tools,
                tool_suites,
            )
        )
    for i, mode in enumerate(codebase.get("failure_modes") or []):
        if str(mode).strip():
            cells.append(
                SignalCell(
                    "failure_mode",
                    f"codebase_card.failure_modes[{i}]",
                    str(mode)[:_CELL_LABEL_LEN],
                    suites=(
                        _ALLOCATION_SUITES
                        if observes_tools or not claim_needs_harness_runtime(str(mode), codebase)
                        else tool_suites
                    ),
                )
            )
    if codebase.get("output_fields") or expected.get("description"):
        semantics_note = (
            "no structural gates exist for this agent by design — judge content "
            "meaning, never envelope shape"
            if construct.get("family") == "open"
            else "key/type/shape checks are deterministic Tier-0 gates — judge MEANING only"
        )
        cells.append(
            SignalCell(
                "output_contract",
                "codebase_card.expected_output",
                "output-contract semantics — the deliverable means what the card's "
                "expected_output declares",
                note=semantics_note,
            )
        )
    constraints = [
        str(c.get("rule") or c) for c in codebase.get("constraints") or [] if str(c).strip()
    ]
    if constraints:
        constraint_suites = (
            _ALLOCATION_SUITES
            if observes_tools or has_generate_observable_constraint(codebase)
            else tool_suites
        )
        cells.append(
            SignalCell(
                "output_contract",
                "codebase_card.constraints",
                _cluster_label("constraint / safety compliance cluster", constraints),
                suites=constraint_suites,
            )
        )
    if any(
        isinstance(t, dict) and str(t.get("name") or "").strip()
        for t in codebase.get("tool_spec") or []
    ):
        cells.append(
            SignalCell(
                "tool_surface",
                "codebase_card.tool_spec",
                "tool selection / usage quality — right tool for the step, sound "
                "arguments, no redundant or looping calls",
                suites=tool_suites,
                note="judge USE quality only — tool-set F1 is not a live gate",
            )
        )
    for path in _trajectory_paths(codebase):
        key = str(path["id"])
        routing = str(path.get("routing") or "").strip()
        cells.append(
            SignalCell(
                "trajectory",
                f"codebase_card.trajectory_map[{key}]",
                f"route selection + adherence for mapped path '{key}'"
                + (f" — {routing[:100]}" if routing else ""),
                suites=tool_suites,
            )
        )
    for i, mode in enumerate(dataset.get("failure_modes") or []):
        if isinstance(mode, dict) and str(mode.get("description") or "").strip():
            cells.append(
                SignalCell(
                    "failure_mode",
                    f"dataset_card.failure_modes[{i}]",
                    str(mode["description"])[:_CELL_LABEL_LEN],
                    suites=(
                        _ALLOCATION_SUITES
                        if observes_tools
                        or not claim_needs_harness_runtime(str(mode["description"]), codebase)
                        else tool_suites
                    ),
                )
            )
    for i, signal in enumerate(dataset.get("quality_signals") or []):
        if isinstance(signal, dict) and str(signal.get("signal") or "").strip():
            cells.append(
                SignalCell(
                    "failure_mode",
                    f"dataset_card.quality_signals[{i}]",
                    str(signal["signal"])[:_CELL_LABEL_LEN],
                )
            )
    if any(role == "reference" for role in (dataset.get("io_mapping") or {}).values()):
        cells.append(
            SignalCell(
                "reference",
                "dataset_card.io_mapping",
                "semantic reference agreement for fields where equivalence needs "
                "judgment (paraphrase, meaning-level match)",
                suites=("generative",),
                note=(
                    "required fields already get a deterministic reference compare — "
                    "author semantic compares only"
                ),
            )
        )
    intents = (report.get("agenda_coverage") or {}).get("uncovered_intents") or []
    for i, intent in enumerate(intents):
        if str(intent).strip():
            cells.append(
                SignalCell(
                    "cohort",
                    f"report.agenda_coverage.uncovered_intents[{i}]",
                    str(intent)[:_CELL_LABEL_LEN],
                )
            )
    return cells


_CITATION_INDEX_RE = re.compile(r"^codebase_card\.trajectory_map\[(\d+)\]")
# All denote the same semantics cell.
_SEMANTICS_ALIAS_PREFIXES = ("codebase_card.output_fields", "codebase_card.output_schema")


def _enabled_membership_roles(evaluators: list[Any]) -> dict[Any, set[str]] | None:
    """pk → enabled roles for evaluators that have membership rows.

    Absent pk means tests/orphans: caller must not filter. A present pk with
    an empty set means every membership is disabled.
    """
    pks = [getattr(ev, "pk", None) for ev in evaluators or []]
    pks = [pk for pk in pks if pk is not None]
    if not pks:
        return None
    from overbae.models import EvalSetMember  # noqa: PLC0415 — avoid import cycle

    rows = EvalSetMember.objects.filter(evaluator_id__in=pks).values_list(
        "evaluator_id", "role", "enabled"
    )
    known: dict[Any, set[str]] = {}
    for eid, role, on in rows:
        known.setdefault(eid, set())
        if on:
            known[eid].add(role)
    return known or None


class SignalAllocation:
    """Tier-0 marks the cells its gates cover, each Tier-1 suite authors the
    residual, and ``claim`` enforces one evaluator per cell per suite."""

    def __init__(self, cells: list[SignalCell], construct: dict[str, str]) -> None:
        self.cells = cells
        self.construct = construct
        self._by_key = {cell.key: cell for cell in cells}
        self._trajectory_ids = [
            cell.key.removeprefix("codebase_card.trajectory_map[").removesuffix("]")
            for cell in cells
            if cell.area == "trajectory"
        ]
        self.claimed: dict[str, dict[str, str]] = {suite: {} for suite in _ALLOCATION_SUITES}
        self.rejected: list[dict[str, str]] = []
        self.unmapped: dict[str, list[str]] = {suite: [] for suite in _ALLOCATION_SUITES}
        self.stale_incumbents: list[dict[str, str]] = []

    def resolve_key(self, citation: str) -> str:
        """Longest-prefix match: `…quality_signals[1]` wins over the
        `…expected_output` cell it also prefixes."""
        citation = str(citation or "").strip().strip("'\"")
        if not citation:
            return ""
        match = _CITATION_INDEX_RE.match(citation)
        if match:
            index = int(match.group(1))
            if index < len(self._trajectory_ids):
                citation = f"codebase_card.trajectory_map[{self._trajectory_ids[index]}]"
        if citation.startswith(_SEMANTICS_ALIAS_PREFIXES) and (
            "codebase_card.expected_output" in self._by_key
        ):
            return "codebase_card.expected_output"
        best = ""
        for key in self._by_key:
            denotes = citation == key or (
                citation.startswith(key) and citation[len(key) : len(key) + 1] in ".["
            )
            if denotes and len(key) > len(best):
                best = key
        if best:
            return best
        # A bare family citation pins to the first cell so it still dedups.
        for key in self._by_key:
            if key.startswith(citation) and key[len(citation) : len(citation) + 1] == "[":
                return key
        return ""

    def mark_tier0(self, tier0_specs: list[EvaluatorSpec]) -> None:
        """Only failure-mode cells are fully covered by a pattern gate; structural
        checks on contract/tool cells cover a different layer."""
        for spec in tier0_specs:
            cell = self._by_key.get(spec.provenance.source)
            if cell is not None and cell.area == "failure_mode" and spec.kind == "deterministic":
                cell.tier0_check = spec.name

    def seed_existing(self, evaluators: list[Any]) -> None:
        """A re-scan mints no second judge for a claimed signal. A stale-contract
        incumbent is left unseeded and recorded so the merge can retire it.
        Disabled memberships do not claim: an emptied generate judge must not
        block the generate-observable remainder."""
        enabled_roles = _enabled_membership_roles(evaluators)
        for evaluator in evaluators or []:
            provenance = (getattr(evaluator, "config", None) or {}).get("provenance") or {}
            key = self.resolve_key(str(provenance.get("source") or ""))
            if not key:
                continue
            try:
                contract = int(provenance.get("authoring_contract") or 0)
            except (TypeError, ValueError):
                contract = 0
            stale = contract != AUTHORING_CONTRACT
            pk = getattr(evaluator, "pk", None)
            known = enabled_roles is not None and pk in enabled_roles
            for suite in roles_for_evaluator(evaluator):
                if suite not in self.claimed:
                    continue
                if known and suite not in enabled_roles[pk]:
                    continue
                if stale:
                    self.stale_incumbents.append(
                        {
                            "suite": suite,
                            "cell": key,
                            "name": evaluator.name,
                            "evaluator_id": str(evaluator.pk),
                        }
                    )
                    continue
                self.claimed[suite].setdefault(key, f"existing:{evaluator.name}")

    def residual(self, suite: str) -> list[SignalCell]:
        return [
            cell
            for cell in self.cells
            if suite in cell.suites and not cell.tier0_check and cell.key not in self.claimed[suite]
        ]

    def claim(self, suite: str, spec: EvaluatorSpec) -> tuple[bool, str]:
        """A citation mapping to no cell is allowed (input faithfulness has no
        card artifact) but tracked."""
        key = self.resolve_key(spec.provenance.source)
        if not key:
            self.unmapped.setdefault(suite, []).append(spec.name)
            return True, ""
        cell = self._by_key[key]
        if cell.tier0_check:
            reason = f"signal cell {key} is already covered by tier-0 check '{cell.tier0_check}'"
            self.rejected.append({"suite": suite, "name": spec.name, "cell": key, "reason": reason})
            return False, reason
        prior = self.claimed.setdefault(suite, {}).get(key)
        if prior:
            reason = f"signal cell {key} is already claimed by '{prior}' in the {suite} suite"
            self.rejected.append({"suite": suite, "name": spec.name, "cell": key, "reason": reason})
            return False, reason
        self.claimed[suite][key] = spec.name
        return True, ""

    def residual_block(self, suite: str) -> str:
        cells = self.residual(suite)
        if not cells:
            return (
                "(no uncovered signal cells remain for this suite — every enumerated "
                "signal already has a deterministic check or a live judge; author "
                "NOTHING unless the pack grounds a dimension no cell enumerates)"
            )
        lines = []
        for cell in cells:
            line = f"- [cite: {cell.key}] ({cell.area}) {cell.label}"
            if cell.note:
                line += f" — NOTE: {cell.note}"
            lines.append(line)
        return "\n".join(lines)

    def report(self) -> dict[str, Any]:
        return {
            "construct": dict(self.construct),
            "cells": [
                {
                    "area": cell.area,
                    "key": cell.key,
                    "label": cell.label,
                    "tier0_check": cell.tier0_check,
                    "claimed": {
                        suite: self.claimed[suite][cell.key]
                        for suite in cell.suites
                        if self.claimed.get(suite, {}).get(cell.key)
                    },
                }
                for cell in self.cells
            ],
            "covered_by_tier0": [cell.key for cell in self.cells if cell.tier0_check],
            "uncovered": {
                suite: [cell.key for cell in self.residual(suite)] for suite in _ALLOCATION_SUITES
            },
            "rejected": list(self.rejected),
            "unmapped": {suite: list(names) for suite, names in self.unmapped.items()},
            "stale_incumbents": list(self.stale_incumbents),
        }


def allocate_signals(
    grounding: EvalGroundingContext,
    tier0_specs: list[EvaluatorSpec],
    *,
    existing_judges: list[Any] | None = None,
    construct: dict[str, str] | None = None,
) -> SignalAllocation:
    construct = construct or resolve_construct(grounding)
    allocation = SignalAllocation(signal_cells(grounding, construct), construct)
    allocation.mark_tier0(tier0_specs)
    allocation.seed_existing(existing_judges or [])
    return allocation
