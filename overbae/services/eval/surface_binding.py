"""Card-derived surface checks for LLM-authored judges. Trace scoring grades
the harness deliverable (card ``output_fields``), so a binding to an
``output_schema``-only key can never resolve there. Repairs are recorded in
the returned notes, never silent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_EVIDENCE_SOURCES = frozenset({"output", "final_output", "structured"})
_METADATA_SOURCES = frozenset({"metadata", "context"})
_TOOL_SOURCES = frozenset({"tool_calls", "tool_call", "toolcalls", "tools_called", "calls"})
_AMBIGUOUS_FIELD_NAMES = frozenset(
    {
        "context",
        "report",
        "output",
        "input",
        "metadata",
        "content",
        "result",
        "status",
        "type",
        "name",
        "value",
        "text",
        "data",
        "error",
        "source",
        "cost",
        "model",
        "steps",
    }
)

# Keys ``_generate_sample`` / ``_generate_per_turn`` / ``normalize_generation``
# actually write. A metadata jsonpath outside this set is harness or agent
# state generate-mode cannot observe — add a key here when the runner grows one.
GENERATE_METADATA_KEYS = frozenset(
    {
        "model",
        "cost",
        "latency_ms",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "steps",
        "max_steps",
        "replay_fuzzy_hits",
        "replay_misses",
        "generation_error",
        "generation_strategy",
        "turns_generated",
        "turn_errors",
        "per_turn",
        "per_turn_scoring",
        "has_expected",
        "output_synthesized_from_reference",
        "row_extra",
        "source",
        "truncated",
        "two_layer",
        "capability_output",
        "model_output",
    }
)

# Also matches sanitation remnants where a stripped ``{output}`` placeholder
# glued prose onto the path ("When.X", ").isInvoice").
_PATH_CHAIN_RE = re.compile(r"(?<![\w$.])(?:\$|[A-Za-z_]\w*)?(?:\.[A-Za-z_]\w*)+")
_BRACED_VAR_RE = re.compile(r"\{([A-Za-z_]\w*)\}")
_METADATA_KEY_RE = re.compile(r"\{metadata\.([A-Za-z_]\w*)\}|(?<![\w$.])metadata\.([A-Za-z_]\w*)")
# Env/config identifiers in constraint rules (DEEP_RESEARCH_CONCURRENCY, MAX_SUBTOPICS).
_HARNESS_SYMBOL_RE = re.compile(r"\b_?[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b")
# Code identifiers in card prose (`report_type`), as opposed to English (`report type`).
_SNAKE_IDENT_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
# Split mixed bullets so a generate-observable clause can survive a harness tail.
_CLAUSE_SPLIT_RE = re.compile(
    r"\s+(?:with|via|using|under|according to)\s+",
    re.I,
)
GOLD_AGREEMENT_CLAIM = "The output agrees with {reference}"
GOLD_AGREEMENT_QUESTION = "Does the output agree with {reference}?"
_REFERENCE_MAPPING_SOURCES = frozenset({"reference", "expected", "expected_output", "ground_truth"})
_CORE_GENERATE_VARS = frozenset(
    {"input", "output", "reference", "expected", "expected_output", "final_output", "ground_truth"}
)
_CONTENT_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{3,}")
_GENERIC_STEMS = frozenset(
    {
        "this",
        "that",
        "then",
        "than",
        "with",
        "from",
        "into",
        "over",
        "also",
        "does",
        "did",
        "been",
        "being",
        "have",
        "has",
        "will",
        "must",
        "should",
        "shall",
        "only",
        "more",
        "less",
        "using",
        "used",
        "such",
        "each",
        "both",
        "between",
        "against",
        "about",
        "after",
        "before",
        "under",
        "during",
        "through",
        "called",
        "configured",
        "invoked",
        "complete",
        "optional",
        "bound",
        "path",
        "self",
        "writing",
        "fall",
        "most",
        "query",
        "they",
        "them",
        "their",
        "which",
        "what",
        "when",
        "where",
        "agent",
        "output",
        "report",
        "input",
        "user",
        "question",
        "produce",
        "produces",
        "produced",
        "emit",
        "emits",
        "emitted",
        "rather",
        "instead",
        "without",
        "within",
        "other",
        "same",
        "true",
        "false",
        "none",
        "null",
        "list",
        "text",
        "string",
        "value",
        "field",
        "name",
        "type",
    }
)


@dataclass(frozen=True)
class SurfaceVocab:
    model_fields: frozenset[str]
    harness_fields: frozenset[str]
    observable_tools: frozenset[str]
    map_callables: frozenset[str]

    @property
    def model_only_fields(self) -> frozenset[str]:
        # A card with no harness layer has a single surface: nothing is model-only.
        if not self.harness_fields:
            return frozenset()
        return self.model_fields - self.harness_fields

    @property
    def unobservable_callables(self) -> frozenset[str]:
        return self.map_callables - self.observable_tools

    @property
    def harness_only_fields(self) -> frozenset[str]:
        # No schema split means one surface: nothing is harness-only.
        if not self.model_fields:
            return frozenset()
        return self.harness_fields - self.model_fields


def card_surface_vocab(card: dict[str, Any] | None) -> SurfaceVocab:
    card = card or {}
    schema = card.get("output_schema") or {}
    model = {str(k) for k in (schema.get("properties") or {})}
    model |= {str(k) for k in schema.get("required_keys") or []}
    harness = {str(k) for k in card.get("output_fields") or {}}
    tools = {
        str(t.get("name")).strip()
        for t in card.get("tool_spec") or []
        if isinstance(t, dict) and str(t.get("name") or "").strip()
    }
    callables: set[str] = set()
    for path in card.get("trajectory_map") or []:
        if isinstance(path, dict):
            callables |= {str(t).strip() for t in path.get("tools") or [] if str(t).strip()}
    return SurfaceVocab(
        frozenset(model), frozenset(harness), frozenset(tools), frozenset(callables)
    )


def _field_refs(text: str, fields: frozenset[str]) -> set[str]:
    """Only the path HEAD counts: a model-only name nested inside a harness
    field is never flagged."""
    refs: set[str] = set()
    for match in _PATH_CHAIN_RE.finditer(text or ""):
        parts = match.group(0).split(".")
        if parts[0] in ("$", "output", ""):
            parts = parts[1:]
        if not parts:
            continue
        if parts[0] in fields:
            refs.add(parts[0])
        elif len(parts) > 1 and parts[1] in fields:
            # Sanitation remnant ("When.isInvoice").
            refs.add(parts[1])
    return refs


def _mentions(name: str, text: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(name)}(?!\w)", text or ""))


def _is_output_field_key(key: str, fields: frozenset[str]) -> bool:
    """A runtime context key that shares a card field name is treated as an
    output binding; cards declare no context-key vocabulary to whitelist against."""
    if "$" in key or key.startswith("output."):
        return True
    tokens = re.findall(r"[A-Za-z_]\w*", key)
    return bool(tokens) and tokens[-1] in fields


def _repair_predicate(
    pred: dict[str, Any], fields: frozenset[str], item_id: str
) -> tuple[dict[str, Any] | None, str]:
    """``None`` with a note makes the item unconditional."""
    op, arg = next(iter(pred.items()))
    if op == "context_equals" and isinstance(arg, dict):
        key = str(arg.get("key") or "")
        # The author gated on the predicate leaf's own name as a context key.
        if key == "output_present":
            if arg.get("value"):
                return {"output_present": True}, (
                    f"item '{item_id}': applies_when context key 'output_present' is the "
                    "predicate leaf, not a context key — rebound to output_present"
                )
            return None, (
                f"item '{item_id}': applies_when gated on output_present=false, which is "
                "not a runnable predicate — made unconditional"
            )
        if _is_output_field_key(key, fields):
            if arg.get("value"):
                return {"output_present": True}, (
                    f"item '{item_id}': applies_when context key {key!r} is an output-field "
                    "binding, not a runtime context key — rebound to output_present"
                )
            return None, (
                f"item '{item_id}': applies_when context key {key!r} is an output-field "
                "binding compared to an empty/false value — made unconditional"
            )
    if op == "context_present":
        key = str(arg or "")
        if key == "output_present" or _is_output_field_key(key, fields):
            return {"output_present": True}, (
                f"item '{item_id}': applies_when context key {key!r} is not a runtime "
                "context key — rebound to output_present"
            )
    return pred, ""


def _jsonpath_head(jsonpath: str) -> str:
    match = re.match(r"\$\.([A-Za-z_]\w*)", jsonpath or "")
    return match.group(1) if match else ""


def _braced_vars(text: str) -> set[str]:
    return set(_BRACED_VAR_RE.findall(text or ""))


def _metadata_keys_in_text(text: str) -> set[str]:
    keys: set[str] = set()
    for first, second in _METADATA_KEY_RE.findall(text or ""):
        if first:
            keys.add(first)
        if second:
            keys.add(second)
    return keys


def _stem(token: str) -> str:
    lower = token.lower()
    if len(lower) > 4 and lower.endswith("s") and not lower.endswith("ss"):
        return lower[:-1]
    return lower


def _content_stems(text: str) -> set[str]:
    return {
        stem
        for raw in _CONTENT_TOKEN_RE.findall(text or "")
        if (stem := _stem(raw)) not in _GENERIC_STEMS
    }


def _claim_text(value: Any) -> str:
    if isinstance(value, dict):
        return str(
            value.get("description") or value.get("rule") or value.get("signal") or ""
        ).strip()
    return str(value or "").strip()


def _constraint_is_output_format(item: dict[str, Any]) -> bool:
    """Same gate as card-constraints: JSON / no-fence on the output string."""
    if str(item.get("kind") or item.get("type") or "").lower() != "output_format":
        return False
    params = item.get("params") if isinstance(item.get("params"), dict) else {}
    if params.get("fence_output") is False:
        return True
    return str(params.get("format") or "").lower() == "json"


def harness_control_symbols(card: dict[str, Any] | None) -> frozenset[str]:
    """ALL-CAPS identifiers declared on card constraints — harness control plane."""
    symbols: set[str] = set()
    for item in (card or {}).get("constraints") or []:
        rule = _claim_text(item)
        if rule:
            symbols.update(_HARNESS_SYMBOL_RE.findall(rule))
    return frozenset(s for s in symbols if len(s) >= 6)


def has_generate_observable_constraint(card: dict[str, Any] | None) -> bool:
    """True when a constraint grades the output string (format), not the harness."""
    for item in (card or {}).get("constraints") or []:
        if isinstance(item, dict) and _constraint_is_output_format(item):
            return True
    return False


def _trajectory_stems(card: dict[str, Any] | None) -> set[str]:
    stems: set[str] = set()
    for path in (card or {}).get("trajectory_map") or []:
        if not isinstance(path, dict):
            continue
        for key in ("id", "routing", "description"):
            stems |= _content_stems(str(path.get(key) or ""))
    return stems


def _control_plane_rules(card: dict[str, Any] | None) -> list[str]:
    rules: list[str] = []
    for item in (card or {}).get("constraints") or []:
        if isinstance(item, dict) and _constraint_is_output_format(item):
            continue
        text = _claim_text(item)
        if text:
            rules.append(text)
    return rules


_JSON_SCHEMA_META = frozenset(
    {"properties", "required", "required_keys", "type", "title", "description", "items"}
)


def card_input_param_names(card: dict[str, Any] | None) -> frozenset[str]:
    """Declared input/config identifiers (``report_type``), not English field names (``query``)."""
    schema = (card or {}).get("input_schema") or {}
    if not isinstance(schema, dict):
        return frozenset()
    props = schema.get("properties")
    raw = props if isinstance(props, dict) and props else schema
    vocab = card_surface_vocab(card)
    allowed = vocab.model_fields | GENERATE_METADATA_KEYS | _JSON_SCHEMA_META
    names: set[str] = set()
    for key in raw:
        name = str(key)
        if (
            name
            and name not in allowed
            and name not in _AMBIGUOUS_FIELD_NAMES
            and (_SNAKE_IDENT_RE.fullmatch(name) or _HARNESS_SYMBOL_RE.fullmatch(name))
        ):
            names.add(name)
    return frozenset(names)


def _prose_harness_idents(text: str, card: dict[str, Any] | None) -> frozenset[str]:
    return frozenset(n for n in card_input_param_names(card) if _mentions(n, text))


def claim_needs_harness_runtime(
    text: str, card: dict[str, Any] | None, *, tools_are_observable: bool = False
) -> bool:
    """Harness state generate-mode cannot observe: sidecars, control-plane
    symbols, snake_case card params, or a non-format constraint. Declared
    tools count unless the graded unit already replayed them."""
    if not (text or "").strip():
        return False
    vocab = card_surface_vocab(card)
    symbols = harness_control_symbols(card) | {
        s for s in _HARNESS_SYMBOL_RE.findall(text) if len(s) >= 6
    }
    tools = vocab.observable_tools | vocab.unobservable_callables
    if any(_mentions(s, text) for s in symbols):
        return True
    if not tools_are_observable and any(_mentions(t, text) for t in tools if t):
        return True
    for field in vocab.harness_only_fields:
        if _mentions(field, text):
            return True
    if _prose_harness_idents(text, card):
        return True
    return any(_item_restates_claim(text, rule) for rule in _control_plane_rules(card))


def _join_observable_clauses(parts: list[str]) -> str:
    body = re.sub(r"\s+", " ", " ".join(parts)).strip(" ,.;")
    if body and body[0].islower():
        body = body[0].upper() + body[1:]
    return body


def observable_claim_text(text: str, card: dict[str, Any] | None) -> str | None:
    """Generate-observable remainder of a card bullet, or None if nothing is left."""
    cleaned = (text or "").strip()
    if not cleaned:
        return None
    if not claim_needs_harness_runtime(cleaned, card):
        return cleaned
    pieces = [p.strip(" ,.;") for p in _CLAUSE_SPLIT_RE.split(cleaned) if p.strip()]
    if len(pieces) <= 1:
        return None
    kept = [p for p in pieces if p and not claim_needs_harness_runtime(p, card)]
    if not kept:
        return None
    remainder = _join_observable_clauses(kept)
    if not remainder or not _content_stems(remainder):
        return None
    if claim_needs_harness_runtime(remainder, card):
        return None
    return remainder


def _item_restates_claim(question: str, claim: str) -> bool:
    cq, cc = _content_stems(question), _content_stems(claim)
    if len(cc) < 2:
        body = (claim or "").rstrip("?").strip().lower()
        return bool(body) and body in (question or "").lower()
    overlap = cq & cc
    if len(overlap) >= 3:
        return True
    return len(overlap) >= 2 and len(overlap) / len(cc) >= 0.5


def _item_covers_cluster_claim(question: str, claim: str) -> bool:
    if is_gold_comparator_claim(question) and (
        _is_query_answering_claim(claim) or is_gold_comparator_claim(claim)
    ):
        return True
    if _is_query_answering_claim(question) and is_gold_comparator_claim(claim):
        return True
    return _item_restates_claim(question, claim)


def _is_query_answering_claim(text: str) -> bool:
    low = (text or "").lower()
    return ("query" in low or "question" in low) and "answer" in low


def is_gold_agreement_text(text: str) -> bool:
    # "citations reference URLs" is not gold: it has the verb, not agreement.
    low = (text or "").lower().replace("-", " ").replace("_", " ")
    return "agree" in low and "reference" in low


_GOLD_LABEL_MATCH = re.compile(r"\b(?:match(?:es)?|equal?s?)\b", re.I)
_GOLD_LABEL_TOPIC = re.compile(
    r"\b(?:gold|intent|label|category|class|ground\s*truth|correct)\b",
    re.I,
)


def is_gold_label_claim(text: str) -> bool:
    """Classification-style gold criteria that do not use agree/reference wording."""
    if is_gold_agreement_text(text):
        return False
    low = (text or "").lower().replace("-", " ").replace("_", " ")
    if _GOLD_LABEL_MATCH.search(low) and _GOLD_LABEL_TOPIC.search(low):
        return True
    return bool(re.search(r"\bcorrect\b", low) and _GOLD_LABEL_TOPIC.search(low))


def is_gold_comparator_claim(text: str) -> bool:
    return is_gold_agreement_text(text) or is_gold_label_claim(text)


def _apply_closed_form_gold(claims: list[str], kind: str) -> list[str]:
    out: list[str] = []
    for claim in claims:
        if _is_query_answering_claim(claim) or is_gold_label_claim(claim):
            if not any(is_gold_comparator_claim(existing) for existing in out):
                out.append(GOLD_AGREEMENT_CLAIM)
        else:
            out.append(claim)
    if kind == "success" and not any(is_gold_comparator_claim(existing) for existing in out):
        out.insert(0, GOLD_AGREEMENT_CLAIM)
    return out


def rebind_query_items_to_gold(
    checklist: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    notes: list[str] = []
    kept: list[dict[str, Any]] = []
    for item in checklist or []:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        question = str(item.get("q") or "")
        if is_gold_comparator_claim(question) and "{reference}" not in question:
            rewritten = dict(item)
            rewritten["q"] = GOLD_AGREEMENT_QUESTION
            notes.append(f"rewrote item '{item.get('id')}' to name {{reference}}")
            kept.append(rewritten)
        elif _is_query_answering_claim(question) and not is_gold_comparator_claim(question):
            rewritten = dict(item)
            rewritten["q"] = GOLD_AGREEMENT_QUESTION
            notes.append(f"rebound item '{item.get('id')}' to reference agreement")
            kept.append(rewritten)
        else:
            kept.append(dict(item))
    return kept, notes


def ensure_reference_variable_mapping(mapping: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kept = [dict(entry) for entry in (mapping or [])]
    if any(
        str(entry.get("source") or "").lower() in _REFERENCE_MAPPING_SOURCES
        or str(entry.get("var") or "").lower() in _REFERENCE_MAPPING_SOURCES
        for entry in kept
    ):
        return kept
    kept.append({"var": "reference", "source": "reference"})
    return kept


_SOURCE_CLAIM_RE = re.compile(
    r"(?:codebase_card|dataset_card)\."
    r"(failure_modes|success_criteria|constraints|expected_output\.quality_signals)"
    r"(?:\[(\d+)\])?"
)


def card_claims_for_source(source: str, card: dict[str, Any] | None) -> list[str]:
    """Card texts a provenance ``source`` path names. Empty when the path is unknown."""
    card = card or {}
    match = _SOURCE_CLAIM_RE.search(str(source or ""))
    if not match:
        return []
    kind, idx = match.group(1), match.group(2)
    if kind == "failure_modes":
        texts = [_claim_text(v) for v in card.get("failure_modes") or []]
    elif kind == "success_criteria":
        texts = [_claim_text(v) for v in card.get("success_criteria") or []]
    elif kind == "constraints":
        texts = [_claim_text(v) for v in card.get("constraints") or []]
    else:
        expected = (
            card.get("expected_output") if isinstance(card.get("expected_output"), dict) else {}
        )
        texts = [_claim_text(v) for v in expected.get("quality_signals") or []]
    texts = [t for t in texts if t]
    if idx is None:
        return texts
    i = int(idx)
    return texts[i : i + 1]


def claim_is_generate_unobservable(text: str, card: dict[str, Any] | None) -> bool:
    if not (text or "").strip():
        return False
    if claim_needs_harness_runtime(text, card):
        return True
    claims, _ = unobservable_generate_claims(card)
    return any(_item_restates_claim(text, c) for c in claims)


def sourced_cluster_unobservable(
    sourced_claims: list[str] | None, card: dict[str, Any] | None
) -> bool:
    """True when every sourced card claim is generate-unobservable.

    A mixed cluster stays per-item: citations next to a deep-mode bullet must
    still grade. A pure harness cluster (empty retriever, semaphore) does not.
    """
    claims = [t for t in (sourced_claims or []) if t]
    return bool(claims) and all(claim_is_generate_unobservable(t, card) for t in claims)


def partition_generate_unobservable(
    checklist: list[dict[str, Any]],
    evaluator,
    card: dict[str, Any] | None,
    *,
    generate_observes_tools: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split items the generate judge must not answer yes/no on.

    Compiled questions often drop the card's when-clause. The evaluator's
    provenance source is the claim that still decides observability.
    """
    source = str(
        ((getattr(evaluator, "config", None) or {}).get("provenance") or {}).get("source") or ""
    )
    sourced = card_claims_for_source(source, card)
    if generate_observes_tools:
        inherit = bool(sourced) and all(
            claim_needs_harness_runtime(t, card, tools_are_observable=True) for t in sourced
        )
    else:
        inherit = sourced_cluster_unobservable(sourced, card)
    applicable: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in checklist or []:
        if not isinstance(item, dict):
            applicable.append(item)
            continue
        question = str(item.get("q") or "")
        # Remainder of a query-answering criterion: inherit from an indexed
        # unobservable source would N/A it with the harness bullets.
        if is_gold_comparator_claim(question):
            applicable.append(item)
            continue
        unobs = (
            inherit or claim_needs_harness_runtime(question, card, tools_are_observable=True)
            if generate_observes_tools
            else inherit or claim_is_generate_unobservable(question, card)
        )
        if unobs:
            excluded.append(
                {
                    "item": item,
                    "reason": "generate-mode cannot observe this claim",
                }
            )
        else:
            applicable.append(item)
    return applicable, excluded


def unobservable_generate_claims(card: dict[str, Any] | None) -> tuple[list[str], frozenset[str]]:
    """Card claims generate-mode cannot verify when replay has no tool loop."""
    card = card or {}
    claims = list(_control_plane_rules(card))
    symbols = harness_control_symbols(card)
    path_stems = _trajectory_stems(card)
    expected = card.get("expected_output") if isinstance(card.get("expected_output"), dict) else {}
    extras: list[str] = []
    extras.extend(_claim_text(v) for v in card.get("failure_modes") or [])
    extras.extend(_claim_text(v) for v in card.get("success_criteria") or [])
    extras.extend(_claim_text(v) for v in expected.get("quality_signals") or [])
    for path in card.get("trajectory_map") or []:
        if isinstance(path, dict):
            extras.extend(str(path.get(k) or "").strip() for k in ("id", "routing", "description"))
    seen = {c.lower() for c in claims}
    for text in extras:
        if not text or text.lower() in seen:
            continue
        stems = _content_stems(text)
        if claim_needs_harness_runtime(text, card) or (path_stems and len(stems & path_stems) >= 2):
            claims.append(text)
            seen.add(text.lower())
    return claims, symbols


def split_generate_observability(
    card: dict[str, Any] | None,
    texts: list[str],
    *,
    generate_observes_tools: bool,
) -> tuple[list[str], list[str]]:
    """Partition card claims into generate-observable vs harness-only.

    A mixed bullet (deliverable clause plus a harness parameter) contributes
    its generate-observable remainder, not the original sentence.
    """
    cleaned = [str(t).strip() for t in texts if str(t).strip()]
    if generate_observes_tools:
        return cleaned, []
    unobs = {c.lower() for c in unobservable_generate_claims(card)[0]}
    observable: list[str] = []
    harness: list[str] = []
    seen_obs: set[str] = set()

    def _add_obs(claim: str) -> None:
        key = claim.lower()
        if key not in seen_obs:
            observable.append(claim)
            seen_obs.add(key)

    for text in cleaned:
        remainder = observable_claim_text(text, card)
        if text.lower() in unobs:
            if remainder and remainder != text:
                _add_obs(remainder)
            harness.append(text)
            continue
        if remainder:
            _add_obs(remainder)
            if remainder != text:
                harness.append(text)
        else:
            harness.append(text)
    return observable, harness


def generate_observable_cluster_claims(
    card: dict[str, Any] | None,
    kind: str,
    *,
    closed_form_reference: bool = False,
) -> list[str]:
    card = card or {}
    if kind == "quality":
        expected = (
            card.get("expected_output") if isinstance(card.get("expected_output"), dict) else {}
        )
        texts = [_claim_text(s) for s in expected.get("quality_signals") or []]
    else:
        texts = [_claim_text(c) for c in card.get("success_criteria") or []]
    observable, _harness = split_generate_observability(card, texts, generate_observes_tools=False)
    if closed_form_reference:
        return _apply_closed_form_gold(observable, kind)
    return observable


def _checklist_slug(text: str, seen: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")[:48] or "claim"
    if base not in seen:
        return base
    n = 2
    while f"{base}-{n}" in seen:
        n += 1
    return f"{base}-{n}"


def _cluster_kind_for_source(source: str) -> str:
    if source.startswith("codebase_card.expected_output.quality_signals"):
        return "quality"
    if source.startswith("codebase_card.success_criteria"):
        return "success"
    return ""


def fill_generate_observable_remainder(
    checklist: list[dict[str, Any]],
    card: dict[str, Any] | None,
    provenance_source: str,
    *,
    generate_observes_tools: bool,
    closed_form_reference: bool = False,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Append generate-observable cluster claims the checklist does not restate."""
    kept = [dict(item) if isinstance(item, dict) else item for item in (checklist or [])]
    kind = _cluster_kind_for_source(provenance_source or "")
    if generate_observes_tools or not card or not kind:
        return kept, []
    questions = [str(item.get("q") or "") for item in kept if isinstance(item, dict)]
    seen_ids = {str(item.get("id") or "") for item in kept if isinstance(item, dict)}
    notes: list[str] = []
    for claim in generate_observable_cluster_claims(
        card, kind, closed_form_reference=closed_form_reference
    ):
        if any(_item_covers_cluster_claim(q, claim) for q in questions if q):
            continue
        item_id = _checklist_slug(claim, seen_ids)
        seen_ids.add(item_id)
        body = claim.rstrip("?").rstrip()
        question = (
            GOLD_AGREEMENT_QUESTION
            if is_gold_comparator_claim(claim)
            else f"Does the output meet this criterion: {body}?"
        )
        kept.append({"id": item_id, "q": question, "weight": 1.0})
        notes.append(f"filled generate-observable remainder '{item_id}'")
        questions.append(question)
    return kept, notes


def uncovered_generate_card_claims(
    card: dict[str, Any] | None,
    checklists: list[list[dict[str, Any]]],
    *,
    generate_observes_tools: bool,
    closed_form_reference: bool = False,
) -> list[str]:
    """Generate-observable success/quality claims no attached checklist restates."""
    if generate_observes_tools or not card:
        return []
    questions = [
        str(item.get("q") or "")
        for checklist in checklists
        for item in checklist or []
        if isinstance(item, dict)
    ]
    uncovered: list[str] = []
    for kind in ("success", "quality"):
        for claim in generate_observable_cluster_claims(
            card, kind, closed_form_reference=closed_form_reference
        ):
            if not any(_item_covers_cluster_claim(q, claim) for q in questions if q):
                uncovered.append(claim)
    return uncovered


def uncovered_gold_comparator_claims(
    card: dict[str, Any] | None,
    checklists: list[list[dict[str, Any]]],
    *,
    generate_observes_tools: bool,
    closed_form_reference: bool = False,
) -> list[str]:
    uncovered = uncovered_generate_card_claims(
        card,
        checklists,
        generate_observes_tools=generate_observes_tools,
        closed_form_reference=closed_form_reference,
    )
    return [claim for claim in uncovered if is_gold_comparator_claim(claim)]


def enforce_surface_bindings(
    *,
    checklist: list[dict[str, Any]],
    variable_mapping: list[dict[str, Any]],
    rubric_md: str,
    card: dict[str, Any] | None,
    grades_live_surface: bool,
    generate_observes_tools: bool = True,
    sourced_claims: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], bool]:
    """``(checklist, variable_mapping, repair_notes, drop_spec)``. Trace specs
    drop model-only fields; generate specs drop harness-only fields,
    generate-unobservable metadata / tool-loop evidence, and card behaviour
    that generate-mode cannot observe."""
    vocab = card_surface_vocab(card)
    known_fields = vocab.model_fields | vocab.harness_fields
    model_only = vocab.model_only_fields if grades_live_surface else frozenset()
    harness_only = vocab.harness_only_fields if not grades_live_surface else frozenset()
    unobservable = vocab.unobservable_callables
    behaviour_claims: list[str] = []
    control_symbols: frozenset[str] = frozenset()
    inherit_unobs = False
    if not grades_live_surface and not generate_observes_tools:
        unobservable = unobservable | vocab.observable_tools
        behaviour_claims, control_symbols = unobservable_generate_claims(card)
        inherit_unobs = sourced_cluster_unobservable(sourced_claims, card)
    notes: list[str] = []

    kept_mapping: list[dict[str, Any]] = []
    dropped_vars: set[str] = set()
    for entry in variable_mapping or []:
        if not isinstance(entry, dict):
            continue
        head = _jsonpath_head(str(entry.get("jsonpath") or ""))
        source = str(entry.get("source") or "output")
        var = str(entry.get("var") or "")
        if head and head in model_only and source in _EVIDENCE_SOURCES:
            notes.append(
                f"dropped variable '{var}': jsonpath binds model-layer-only "
                f"field '{head}' absent from the live deliverable"
            )
            dropped_vars.add(var)
            continue
        if head and head in harness_only and source in (_EVIDENCE_SOURCES | _METADATA_SOURCES):
            notes.append(
                f"dropped variable '{var}': jsonpath binds harness-only "
                f"field '{head}' that generate-mode cannot observe"
            )
            dropped_vars.add(var)
            continue
        if (
            not grades_live_surface
            and source in _METADATA_SOURCES
            and head
            and head not in GENERATE_METADATA_KEYS
        ):
            notes.append(
                f"dropped variable '{var}': metadata.{head} is not generate-mode runner telemetry"
            )
            dropped_vars.add(var)
            continue
        if not grades_live_surface and not generate_observes_tools and source in _TOOL_SOURCES:
            notes.append(
                f"dropped variable '{var}': generate-mode replay has no structured tool calls"
            )
            dropped_vars.add(var)
            continue
        kept_mapping.append(entry)

    kept: list[dict[str, Any]] = []
    for item in checklist or []:
        if not isinstance(item, dict):
            kept.append(item)
            continue
        item = dict(item)
        item_id = str(item.get("id") or "?")
        question = str(item.get("q") or "")
        refs = _field_refs(question, known_fields)
        refs |= _braced_vars(question) & known_fields
        declared_field = str(item.get("field") or "").strip()
        if declared_field:
            refs.add(declared_field)
        bad_model = refs & model_only
        if bad_model:
            notes.append(
                f"dropped item '{item_id}': references model-layer-only field(s) "
                f"{sorted(bad_model)} absent from the live deliverable (card output_fields)"
            )
            continue
        bad_harness = refs & harness_only
        if bad_harness:
            notes.append(
                f"dropped item '{item_id}': references harness-only field(s) "
                f"{sorted(bad_harness)} that generate-mode (model + replay) cannot observe"
            )
            continue
        if not grades_live_surface:
            bad_meta = _metadata_keys_in_text(question) - GENERATE_METADATA_KEYS
            if bad_meta:
                notes.append(
                    f"dropped item '{item_id}': references generate-unobservable "
                    f"metadata key(s) {sorted(bad_meta)}"
                )
                continue
            named = _braced_vars(question) & dropped_vars
            if named:
                notes.append(
                    f"dropped item '{item_id}': references dropped binding(s) {sorted(named)}"
                )
                continue
            mentioned = sorted(
                n
                for n in (harness_only | dropped_vars) - _AMBIGUOUS_FIELD_NAMES
                if _mentions(n, question)
            )
            if mentioned:
                notes.append(
                    f"dropped item '{item_id}': names generate-unobservable field(s) {mentioned}"
                )
                continue
            if not generate_observes_tools and (
                "tool_calls" in _braced_vars(question) or _mentions("tool_calls", question)
            ):
                notes.append(
                    f"dropped item '{item_id}': requires tool_calls that generate-mode "
                    "replay does not populate"
                )
                continue
            named_sym = sorted(s for s in control_symbols if _mentions(s, question))
            if named_sym:
                notes.append(
                    f"dropped item '{item_id}': names harness control-plane "
                    f"symbol(s) {named_sym} that generate-mode cannot observe"
                )
                continue
            named_idents = sorted(_prose_harness_idents(question, card))
            if named_idents:
                notes.append(
                    f"dropped item '{item_id}': names generate-unobservable "
                    f"identifier(s) {named_idents}"
                )
                continue
            body = question
            lower_q = question.lower()
            marker = "does the output meet this criterion:"
            if marker in lower_q:
                body = question[lower_q.index(marker) + len(marker) :].strip().rstrip("?")
            if not _content_stems(body):
                notes.append(f"dropped item '{item_id}': filled remainder is not a checkable claim")
                continue
            if any(_item_restates_claim(question, claim) for claim in behaviour_claims):
                notes.append(
                    f"dropped item '{item_id}': restates generate-unobservable card behaviour"
                )
                continue
            if inherit_unobs:
                if is_gold_comparator_claim(question):
                    kept.append(item)
                    continue
                notes.append(
                    f"dropped item '{item_id}': sourced card claim is generate-unobservable"
                )
                continue
        bad_tools = sorted(t for t in unobservable if _mentions(t, question))
        if bad_tools:
            notes.append(
                f"dropped item '{item_id}': requires callable(s) {bad_tools} not declared "
                "in card tool_spec, so they are never observable as tool calls"
                if grades_live_surface or generate_observes_tools
                else (
                    f"dropped item '{item_id}': requires tool(s) {bad_tools} that "
                    "generate-mode replay does not populate"
                )
            )
            continue
        pred = item.get("applies_when")
        if isinstance(pred, dict) and len(pred) == 1:
            repaired, note = _repair_predicate(pred, frozenset(known_fields), item_id)
            if note:
                notes.append(note)
                if repaired is None:
                    item.pop("applies_when", None)
                else:
                    item["applies_when"] = repaired
        kept.append(item)

    if not grades_live_surface:
        text = " ".join(str(item.get("q") or "") for item in kept if isinstance(item, dict))
        used = _braced_vars(text) | _metadata_keys_in_text(text)
        filtered_mapping: list[dict[str, Any]] = []
        for entry in kept_mapping:
            var = str(entry.get("var") or "")
            if var in _CORE_GENERATE_VARS or var in used:
                filtered_mapping.append(entry)
                continue
            notes.append(
                f"dropped unused variable '{var}': not referenced in the generate checklist"
            )
        kept_mapping = filtered_mapping

    drop = False
    if checklist and not kept:
        drop = True
        notes.append(
            "every checklist item was dropped by surface validation — the spec grades a "
            "surface the graded role cannot observe"
        )
    elif not checklist and (_field_refs(rubric_md, known_fields) & (model_only | harness_only)):
        drop = True
        notes.append(
            "rubric grades a surface the graded role cannot observe, with no checklist to repair"
        )
    elif (
        not checklist
        and not grades_live_surface
        and (_metadata_keys_in_text(rubric_md) - GENERATE_METADATA_KEYS)
    ):
        drop = True
        notes.append("rubric grades generate-unobservable metadata, with no checklist to repair")
    return kept, kept_mapping, notes, drop
