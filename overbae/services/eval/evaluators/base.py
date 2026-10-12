"""Evaluator engine core. Families return :class:`ScoreDraft` rather than ORM
rows; the task layer persists them."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

from overbae.services.eval import predicates

logger = logging.getLogger(__name__)


# Values MUST match ``Score.Outcome``; not imported so families stay free of Django.
OUTCOME_SCORED = "scored"
OUTCOME_ABSTAINED = "abstained"
OUTCOME_NOT_APPLICABLE = "not_applicable"
OUTCOME_ERROR = "error"

# Which system is asking. Generative runs grade a dataset against a golden and
# own ``gen_judge``; trace scoring grades live production traces and owns
# ``judge``. The two never share a judge implementation — the surface travels in
# ``ctx`` so nested dispatch (dag leaves, cascade) stays on one side.
SURFACE_GENERATIVE = "generative"
SURFACE_TRACE_SCORING = "trace_scoring"


@dataclass
class EvalUnit:
    trajectory: dict[str, Any] = field(default_factory=dict)
    structured: dict[str, Any] = field(default_factory=dict)
    expected: Any = None
    sample_id: str = ""
    # The capability's declared I/O contract. Trigger inference must see the
    # same map the compiler used (input + output + schema), not output alone.
    output_fields: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    input_schema: dict[str, Any] = field(default_factory=dict)
    # Canonical grounding variables collected for the dataset. Lets grounding
    # evaluators resolve real evidence instead of abstaining when the
    # datapoint's expected_output is only an instruction shard.
    reference_context: dict[str, Any] = field(default_factory=dict)
    codebase_card: dict[str, Any] = field(default_factory=dict)

    def contract_card(self) -> dict[str, Any]:
        """The same three keys ``schema_field_map`` reads from a codebase card."""
        return {
            "output_fields": self.output_fields or {},
            "output_schema": self.output_schema or {},
            "input_schema": self.input_schema or {},
        }

    @classmethod
    def from_sample(cls, sample) -> EvalUnit:
        output_fields, output_schema, input_schema = _capability_output_contract(sample)
        return cls(
            trajectory=sample.trajectory or {},
            structured=sample.structured or {},
            expected=sample.expected,
            sample_id=str(sample.id),
            output_fields=output_fields,
            output_schema=output_schema,
            input_schema=input_schema,
            reference_context=_dataset_reference_context(sample),
            codebase_card=_capability_codebase_card(sample),
        )


def contract_from_capability(capability) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """``(output_fields, output_schema, input_schema)``. Empty dicts when absent."""
    if capability is None:
        return {}, {}, {}
    output_fields = capability.output_fields if isinstance(capability.output_fields, dict) else {}
    output_schema = capability.output_schema if isinstance(capability.output_schema, dict) else {}
    input_schema = capability.input_schema if isinstance(capability.input_schema, dict) else {}
    return output_fields, output_schema, input_schema


def _capability_output_contract(sample) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """Empty dicts when the sample is not bound to a dataset or capability."""
    try:
        capability = sample.run.dataset.capability
    except AttributeError:
        return {}, {}, {}
    return contract_from_capability(capability)


def _capability_codebase_card(sample) -> dict[str, Any]:
    try:
        capability = sample.run.dataset.capability
    except AttributeError:
        return {}
    if capability is None:
        return {}
    meta = (
        capability.improvement_metadata if isinstance(capability.improvement_metadata, dict) else {}
    )
    card = meta.get("capability_card") or {}
    card = {} if not isinstance(card, dict) else dict(card)
    output_fields, output_schema, input_schema = contract_from_capability(capability)
    if output_fields:
        card.setdefault("output_fields", output_fields)
    if output_schema:
        card.setdefault("output_schema", output_schema)
    if input_schema:
        card.setdefault("input_schema", input_schema)
    return card


def _dataset_reference_context(sample) -> dict[str, Any]:
    try:
        dataset = sample.run.dataset
    except AttributeError:
        return {}
    if dataset is None:
        return {}
    try:
        from overbae.services.eval.grounding import build_reference_context  # noqa: PLC0415

        return build_reference_context(dataset)
    except Exception:  # noqa: BLE001 — grounding is additive, never fatal
        logger.warning("reference_context resolve failed for sample %s", getattr(sample, "id", "?"))
        return {}


@dataclass
class ScoreDraft:
    name: str
    data_type: str = "numeric"
    value: float | None = None
    string_value: str = ""
    passed: bool | None = None
    # Persistence coerces this so ``value is None`` ⇔ ``outcome != scored``.
    outcome: str = OUTCOME_SCORED
    reasoning: str = ""
    scope: str = "sample"
    target_ref: str = ""
    sub_scores: list[dict[str, Any]] = field(default_factory=list)
    failure_role: str = "none"
    judge_trace_id: str = ""
    cost: float = 0.0
    latency_ms: float = 0.0


class JudgeResult(BaseModel):
    # Reasoning precedes score/label: autoregressive models reason before they answer.
    items: list[JudgeItem] = Field(default_factory=list)
    reasoning: str = Field(default="", description="Concise overall rationale")
    score: float = Field(description="Overall score in the rubric's range")
    label: str = Field(default="", description="Categorical verdict, if applicable")
    evidence: list[str] = Field(default_factory=list, description="Quoted supporting spans")
    delivery: str = Field(
        default="",
        description=("delivered | delivered_wrong | outstanding | blocked | clarify | first_park"),
    )
    clears_outstanding: bool = Field(
        default=False,
        description="True only when this turn resolved the outstanding ask",
    )
    root_cause: str = Field(
        default="",
        description="Single failing span name or tool; empty on a pass",
    )
    root_cause_reason: str = Field(
        default="",
        description="Why that span is the miss; empty on a pass",
    )
    abstained: bool = Field(
        default=False,
        description=(
            "True only when the evidence contains nothing the rubric could "
            "grade in either direction (the behavior under evaluation never "
            "occurred); an abstention carries no score"
        ),
    )
    # Defaults keep cached pre-gates results parsing.
    gates: list[GateVerdict] = Field(
        default_factory=list,
        description="One entry per [GATE] checklist item: its id and pass/fail verdict",
    )


class GateVerdict(BaseModel):
    id: str = ""
    passed: bool | None = None


class JudgeItem(BaseModel):
    id: str = ""
    verdict: bool | None = None
    score: float | None = None
    reasoning: str = ""
    not_applicable: bool = Field(
        default=False,
        description=(
            "True when the item concerns an action type absent from this "
            "unit's evidence; excluded from the overall score, never a pass"
        ),
    )


JudgeResult.model_rebuild()


_TOKEN_RE = re.compile(r"\.\.|\.([A-Za-z_][\w]*)|\[([^\]]+)\]|([A-Za-z_][\w]*)")


def resolve_jsonpath(obj: Any, path: str) -> list[Any]:
    """A dependency-free JSONPath subset: ``$``, ``.key``, ``['key']``,
    ``[index]`` including negative, ``[*]``, and ``..key`` recursive descent."""
    if not path or path in ("$", "$.", ""):
        return [obj]
    expr = path[1:] if path.startswith("$") else path
    current: list[Any] = [obj]
    i = 0
    pending_descent = False
    while i < len(expr):
        m = _TOKEN_RE.match(expr, i)
        if not m:
            i += 1
            continue
        i = m.end()
        if m.group(0) == "..":
            pending_descent = True
            continue
        key = m.group(1) or m.group(3)
        bracket = m.group(2)
        if key is not None:
            current = _step_key(current, key, pending_descent)
            pending_descent = False
        elif bracket is not None:
            current = _step_bracket(current, bracket.strip(), pending_descent)
            pending_descent = False
    return current


def _descend(obj: Any, key: str) -> list[Any]:
    out: list[Any] = []
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k == key:
                    out.append(v)
                stack.append(v)
        elif isinstance(node, list):
            stack.extend(node)
    return out


def _step_key(current: list[Any], key: str, descent: bool) -> list[Any]:
    out: list[Any] = []
    for node in current:
        if descent:
            out.extend(_descend(node, key))
        elif isinstance(node, dict) and key in node:
            out.append(node[key])
    return out


def _step_bracket(current: list[Any], token: str, descent: bool) -> list[Any]:
    out: list[Any] = []
    for node in current:
        items = _descend_collect(node) if descent else [node]
        for it in items:
            if token == "*":
                if isinstance(it, list):
                    out.extend(it)
                elif isinstance(it, dict):
                    out.extend(it.values())
            elif token.startswith("'") or token.startswith('"'):
                k = token.strip("'\"")
                if isinstance(it, dict) and k in it:
                    out.append(it[k])
            else:
                try:
                    idx = int(token)
                    if isinstance(it, list) and -len(it) <= idx < len(it):
                        out.append(it[idx])
                except ValueError:
                    if isinstance(it, dict) and token in it:
                        out.append(it[token])
    return out


def _descend_collect(obj: Any) -> list[Any]:
    out = [obj]
    stack = [obj]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for v in node.values():
                out.append(v)
                stack.append(v)
        elif isinstance(node, list):
            for v in node:
                out.append(v)
                stack.append(v)
    return out


def _source_object(unit: EvalUnit, source: str) -> Any:
    traj = unit.trajectory or {}
    messages = traj.get("messages", [])
    if source in ("output", "final_output"):
        return traj.get("final_output", "")
    if source == "input":
        # Without the system message a terse classification output gets mis-scored.
        system_content = next(
            (m.get("content") or "" for m in messages if m.get("role") == "system"),
            None,
        )
        user_content = next(
            (m.get("content") or "" for m in messages if m.get("role") == "user"),
            "",
        )
        if system_content:
            return f"[System]\n{system_content}\n\n[User]\n{user_content}"
        return user_content
    if source == "last_user_input":
        last = None
        for m in messages:
            if m.get("role") == "user":
                last = m.get("content", "")
        return last or ""
    if source == "all_user_messages":
        parts = [m.get("content", "") for m in messages if m.get("role") == "user"]
        return "\n\n".join(parts)
    if source == "conversation":
        lines = []
        for m in messages:
            role = m.get("role", "")
            content = m.get("content") or ""
            if role in ("system", "user", "assistant") and content:
                prefix = {"system": "System", "user": "User", "assistant": "Assistant"}.get(
                    role, role.capitalize()
                )
                lines.append(f"[{prefix}]\n{content}")
        return "\n\n".join(lines)
    if source in ("reference", "expected", "expected_output"):
        return unit.expected
    if source in ("span_tree", "spans", "execution"):
        return traj.get("span_tree") or []
    if source == "messages":
        return messages
    if source == "trajectory":
        return traj.get("span_tree") or messages
    if source == "tool_calls":
        return (unit.structured.get("tool_graph") or {}).get("nodes", [])
    if source == "tool_definitions":
        return traj.get("tool_definitions", [])
    if source == "metadata":
        return traj.get("metadata", {})
    if source == "structured":
        return unit.structured
    if source == "sample":
        return {"trajectory": traj, "structured": unit.structured, "expected": unit.expected}
    if source == "runtime_expectations":
        return (traj.get("runtime") or {}).get("expectations", [])
    if source == "runtime_context":
        return (traj.get("runtime") or {}).get("context", {})
    if source == "runtime_checkpoints":
        return (traj.get("runtime") or {}).get("checkpoints", [])
    if source in ("prompt_template", "prompt_kwargs"):
        key = "template" if source == "prompt_template" else "kwargs"
        records = (traj.get("runtime") or {}).get("prompt_records") or []
        values = [r.get(key) for r in records if r.get(key)]
        if not values:
            return None
        # Multi-call agents get the ordered list so nothing is silently dropped.
        return values[0] if len(values) == 1 else values
    return traj.get(source)


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str, ensure_ascii=False)


# A mapping is authored against one capability's schema; the resolver below
# adapts to other shapes without fabricating absent evidence.

_BLANK_STRINGS = frozenset({"", "[]", "{}", "null", "none", '""'})

_CANONICAL_SOURCES: dict[str, str] = {
    "span_tree": "span_tree",
    "spans": "span_tree",
    "execution": "span_tree",
    "trajectory": "trajectory",
    "tool_calls": "tool_calls",
    "tool_call": "tool_calls",
    "toolcalls": "tool_calls",
    "tools_called": "tool_calls",
    "calls": "tool_calls",
    "actions": "tool_calls",
    "tool_definitions": "tool_definitions",
    "tool_defs": "tool_definitions",
    "tool_spec": "tool_definitions",
    "tool_specs": "tool_definitions",
    "available_tools": "tool_definitions",
    "final_output": "final_output",
    "output": "final_output",
    "answer": "final_output",
    "response": "final_output",
    "generation": "final_output",
    "prediction": "final_output",
    "reference": "reference",
    "expected": "reference",
    "expected_output": "reference",
    "ground_truth": "reference",
    "groundtruth": "reference",
    "gold": "reference",
    "target": "reference",
    "input": "input",
    "prompt": "input",
    "question": "input",
    "query": "input",
    "task": "input",
    "conversation": "conversation",
    "transcript": "conversation",
    "history": "conversation",
    "messages": "messages",
    "metadata": "metadata",
    "context": "metadata",
}

_SEMANTIC_ALIASES: dict[str, list[str]] = {
    "recommendations": ["recommendations", "recs", "suggestions", "advice"],
    "summary": ["summary", "overview", "synopsis", "tldr"],
    "proposed_fixes": ["proposed_fixes", "fixes", "remediations", "repairs"],
    "validation_issues": ["validation_issues", "issues", "problems", "violations"],
    "findings": ["findings", "observations", "insights"],
    "clusters": ["clusters", "groupings", "segments"],
    "diversity": ["diversity", "variety"],
    "agenda_coverage": ["agenda_coverage", "coverage"],
    "dataset_card": ["dataset_card", "datacard"],
    "dataset_facts": ["dataset_facts", "facts"],
}

# Vars meaning "the whole container" may fall back to the source object.
_CONTAINER_VARS = frozenset(_CANONICAL_SOURCES)

_REFERENCE_SOURCE_NAMES = frozenset(
    {"reference", "expected", "expected_output", "ground_truth", "groundtruth", "gold", "target"}
)

# Normalized var name (plus synonyms) → key in ``EvalUnit.reference_context``.
_GROUNDING_VAR_KEYS: dict[str, str] = {
    "dataset_facts": "dataset_facts",
    "facts": "dataset_facts",
    "dataset_card": "dataset_card",
    "datacard": "dataset_card",
    "known_row_ids": "known_row_ids",
    "row_ids": "known_row_ids",
    "row_signatures": "known_row_ids",
    "workshop_report": "workshop_report",
    "analysis_report": "workshop_report",
    "report": "workshop_report",
    "tool_spec": "tool_spec",
    "tool_specs": "tool_spec",
    "tool_vocabulary": "tool_spec",
    "available_tools": "tool_spec",
}


@dataclass
class ResolvedVariable:
    value: str
    strategy: str  # explicit_source | explicit_path | schema | semantic | default | absent
    shape: str


def _normalize_var(var: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(var).lower()).strip("_")


def _maybe_parse(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped[:1] in "{[":
        try:
            return json.loads(stripped)
        except (ValueError, TypeError):
            return value
    return value


def _is_chatml_message(value: Any) -> bool:
    return isinstance(value, dict) and "role" in value and "content" in value


def _unwrap_chatml_content(base: Any) -> Any:
    """A trace-derived ``expected_output`` is a ChatML envelope while the authored
    JSONPath targets the payload. ``None`` for a non-ChatML base."""
    message = None
    if _is_chatml_message(base):
        message = base
    elif isinstance(base, list) and base and all(_is_chatml_message(m) for m in base):
        message = next((m for m in reversed(base) if m.get("content")), base[-1])
    if message is None:
        return None
    return _maybe_parse(message.get("content"))


def _is_nonempty(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in _BLANK_STRINGS
    if isinstance(value, (list, dict, tuple, set)):
        return len(value) > 0
    return True


def detect_shape(value: Any) -> str:
    if not _is_nonempty(value):
        return "empty"
    parsed = _maybe_parse(value)
    if isinstance(parsed, dict):
        if isinstance(parsed.get("messages"), list):
            return "chatml"
        if "tool_graph" in parsed:
            return "tool_graph"
        return "json_object"
    if isinstance(parsed, list):
        if parsed and all(isinstance(x, dict) and x.get("role") for x in parsed):
            return "chatml"
        if parsed and all(isinstance(x, dict) and ("tool" in x or "name" in x) for x in parsed):
            return "tool_calls"
        return "json_array"
    if isinstance(parsed, str):
        return "text"
    return "scalar"


def _schema_field_names(unit: EvalUnit) -> set[str]:
    names: set[str] = set()
    fields = unit.output_fields if isinstance(unit.output_fields, dict) else {}
    names |= {_normalize_var(k) for k in fields}
    schema = unit.output_schema if isinstance(unit.output_schema, dict) else {}
    properties = schema.get("properties")
    if isinstance(properties, dict):
        names |= {_normalize_var(k) for k in properties}
    elif schema:
        names |= {_normalize_var(k) for k in schema}
    return names


def _deep_find(obj: Any, keys: list[str]) -> tuple[Any, bool]:
    """Shallowest-first; ambiguous means more than one key matched at that depth."""
    keyset = {k.lower() for k in keys}
    level = [_maybe_parse(obj)]
    while level:
        matches: list[Any] = []
        nxt: list[Any] = []
        for node in level:
            node = _maybe_parse(node)
            if isinstance(node, dict):
                for key, val in node.items():
                    if str(key).lower() in keyset and _is_nonempty(val):
                        matches.append(val)
                nxt.extend(node.values())
            elif isinstance(node, list):
                nxt.extend(node)
        if matches:
            return matches[0], len(matches) > 1
        level = nxt
    return None, False


def _resolve_entry(unit: EvalUnit, entry: dict[str, Any]) -> ResolvedVariable:
    var = str(entry.get("var") or "")
    source = entry.get("source") or ""
    jsonpath = entry.get("jsonpath")
    norm = _normalize_var(var)

    # An explicit source resolving to NOTHING falls through only for a known
    # grounding key, so collected context can supply it.
    if source and not jsonpath:
        base = _source_object(unit, source)
        if _is_nonempty(base) or norm not in _GROUNDING_VAR_KEYS:
            return ResolvedVariable(_stringify(base), "explicit_source", detect_shape(base))

    if jsonpath:
        effective_source = source or _infer_source(norm)
        path_base = _maybe_parse(_source_object(unit, effective_source))
        if _is_reference_source(effective_source):
            unwrapped = _unwrap_chatml_content(path_base)
            if unwrapped is not None:
                path_base = unwrapped
        matches = resolve_jsonpath(path_base, jsonpath)
        resolved = matches[0] if len(matches) == 1 else matches
        if _is_nonempty(resolved):
            return ResolvedVariable(_stringify(resolved), "explicit_path", detect_shape(resolved))

    # Before the schema tier, so an explicit reference request is never
    # shadowed by a same-named output field.
    if _is_reference_source(source):
        grounded = _grounding_lookup(unit, norm, jsonpath)
        if _is_nonempty(grounded):
            return ResolvedVariable(_stringify(grounded), "dataset_context", detect_shape(grounded))

    if norm in _schema_field_names(unit):
        found, _ = _deep_find(_source_object(unit, "final_output"), _alias_keys(norm, var))
        if _is_nonempty(found):
            return ResolvedVariable(_stringify(found), "schema", detect_shape(found))

    canonical = _CANONICAL_SOURCES.get(norm)
    if canonical:
        value = _source_object(unit, canonical)
        if _is_nonempty(value):
            return ResolvedVariable(_stringify(value), "semantic", detect_shape(value))

    container = _source_object(unit, source) if source else _source_object(unit, "final_output")
    found, is_ambiguous = _deep_find(container, _alias_keys(norm, var))
    if _is_nonempty(found):
        strategy = "semantic_ambiguous" if is_ambiguous else "semantic"
        return ResolvedVariable(_stringify(found), strategy, detect_shape(found))

    tree = (unit.trajectory or {}).get("span_tree")
    if tree:
        found, is_ambiguous = _deep_find(tree, _alias_keys(norm, var))
        if _is_nonempty(found):
            strategy = "semantic_ambiguous" if is_ambiguous else "semantic"
            return ResolvedVariable(_stringify(found), strategy, detect_shape(found))

    # Grounding by name for a var whose source was not a reference alias.
    if norm in _GROUNDING_VAR_KEYS:
        grounded = _grounding_lookup(unit, norm, jsonpath)
        if _is_nonempty(grounded):
            return ResolvedVariable(_stringify(grounded), "dataset_context", detect_shape(grounded))

    # A specific missing content key stays absent so judges abstain honestly.
    if norm in _CONTAINER_VARS:
        value = (
            _source_object(unit, source) if source else _source_object(unit, _infer_source(norm))
        )
        if _is_nonempty(value):
            return ResolvedVariable(_stringify(value), "default", detect_shape(value))

    return ResolvedVariable("", "absent", "empty")


def _is_reference_source(source: Any) -> bool:
    return _normalize_var(str(source or "")) in _REFERENCE_SOURCE_NAMES


def _grounding_lookup(unit: EvalUnit, norm: str, jsonpath: str | None = None) -> Any:
    ctx = unit.reference_context or {}
    if not ctx:
        return None
    key = _GROUNDING_VAR_KEYS.get(norm, norm)
    value = ctx.get(key)
    if not _is_nonempty(value):
        # A re-sourced binding may carry a jsonpath rooted at a grounding key itself.
        if jsonpath:
            matches = resolve_jsonpath(ctx, jsonpath)
            resolved = matches[0] if len(matches) == 1 else matches
            if _is_nonempty(resolved):
                return resolved
        return None
    if jsonpath:
        # A path that misses (typically mirroring the grounding key name) falls
        # back to the whole value: the named evidence WAS collected.
        matches = resolve_jsonpath(_maybe_parse(value), jsonpath)
        resolved = matches[0] if len(matches) == 1 else matches
        if _is_nonempty(resolved):
            return resolved
    return value


def _alias_keys(norm: str, var: str) -> list[str]:
    aliases = _SEMANTIC_ALIASES.get(norm, [])
    return list(dict.fromkeys([var, norm, *aliases]))


def _infer_source(norm: str) -> str:
    return _CANONICAL_SOURCES.get(norm, "final_output")


def resolve_variables_detailed(
    unit: EvalUnit, mapping: list[dict[str, Any]]
) -> dict[str, ResolvedVariable]:
    out: dict[str, ResolvedVariable] = {}
    for entry in mapping or []:
        var = entry.get("var")
        if not var:
            continue
        out[var] = _resolve_entry(unit, entry)
    if out:
        logger.debug(
            "resolve_variables: %s",
            {v: f"{r.strategy}/{r.shape}" for v, r in out.items()},
        )
    return out


def resolve_variables(unit: EvalUnit, mapping: list[dict[str, Any]]) -> dict[str, str]:
    return {var: rv.value for var, rv in resolve_variables_detailed(unit, mapping).items()}


def resolve_one(
    unit: EvalUnit,
    var: str,
    *,
    source: str | None = None,
    jsonpath: str | None = None,
) -> ResolvedVariable:
    """The same tiers the judge uses, so every kind extracts evidence identically."""
    return _resolve_entry(unit, {"var": var, "source": source or "", "jsonpath": jsonpath})


def default_variable_mapping() -> list[dict[str, Any]]:
    return [
        {"var": "input", "source": "input"},
        {"var": "output", "source": "output"},
        {"var": "reference", "source": "reference"},
    ]


def normalize_numeric(raw: float, evaluator) -> tuple[float, bool | None]:
    """``passed`` exists only for boolean evaluators; a graded score is never thresholded."""
    lo, hi = evaluator.score_min, evaluator.score_max
    value = max(lo, min(hi, float(raw)))
    passed = None
    if getattr(evaluator, "score_type", "numeric") == "boolean":
        threshold = evaluator.pass_threshold if evaluator.pass_threshold is not None else hi
        passed = value >= threshold
    return value, passed


# Auto-generated evaluators routinely have empty ``choices``.
_CANONICAL_CHOICE_MAP: dict[str, float] = {
    "pass": 1.0,
    "passed": 1.0,
    "yes": 1.0,
    "true": 1.0,
    "correct": 1.0,
    "full_credit": 1.0,
    "fail": 0.0,
    "failed": 0.0,
    "no": 0.0,
    "false": 0.0,
    "incorrect": 0.0,
    "partial": 0.5,
    "partial_credit": 0.5,
}


def _normalize_label(label: str) -> str:
    cleaned = re.sub(r"[^\w]+", "_", str(label).strip().lower())
    return cleaned.strip("_")


def map_choice(label: str, evaluator) -> tuple[float | None, bool | None]:
    """``passed`` is always ``None``: a categorical verdict is a classification,
    not a boolean failure."""
    for c in evaluator.choices or []:
        if str(c.get("label")).lower() == str(label).lower():
            return float(c.get("value", 0.0)), None

    canonical = _CANONICAL_CHOICE_MAP.get(_normalize_label(label))
    if canonical is not None:
        return canonical, None

    return None, None


# The evidence under judgement (the model's behavior), as opposed to context
# (input) or ground truth (reference). A scoped evaluator resolving ALL of these
# to empty has nothing to grade, so its score is flagged low-provenance.
_EVIDENCE_SOURCES = frozenset(
    {
        "output",
        "last_assistant_output",
        "trajectory",
        "messages",
        "conversation",
        "tool_calls",
        "tool_definitions",
        "final_output",
        "structured",
        "step",
    }
)
_EVIDENCE_SCOPES = frozenset({"final_output", "trajectory", "tool", "step"})
_BLANK_RESOLUTIONS = frozenset({"", "[]", "{}", "null", "none", '""'})


def _is_blank_resolution(value: str) -> bool:
    return (value or "").strip().lower() in _BLANK_RESOLUTIONS


def insufficient_evidence_reason(
    unit: EvalUnit,
    evaluator,
    mapping: list[dict[str, Any]] | None = None,
    resolved: dict[str, ResolvedVariable] | None = None,
) -> str | None:
    """The vacuous-pass risk — scoring 1.0 because "the trajectory is empty" —
    is not a missing-variable problem: the variables resolve, just to empty
    strings and lists. Callers do NOT block on the reason; they score anyway and
    stamp the result low-provenance for the UI.

    ``mapping`` and its ``resolved`` detail are passed in so the gate check
    reuses one resolution; called directly they default to a fresh resolve.
    """
    if mapping is None:
        mapping = evaluator.variable_mapping or default_variable_mapping()

    # A gate is a hard pass/fail contract, so a fully absent variable must
    # abstain rather than fire a confident ``False`` invented from missing
    # evidence — including gate vars sourced from input/expected, which are not
    # in ``_EVIDENCE_SOURCES``. Gates exist only on boolean evaluators; a stale
    # gate flag on a graded one must not reach this path.
    has_gates = getattr(evaluator, "score_type", "numeric") == "boolean" and any(
        item.get("gate") for item in (evaluator.checklist or [])
    )
    if has_gates:
        if resolved is None:
            resolved = resolve_variables_detailed(unit, mapping)
        # Empty counts as missing: a var that resolved to nothing, whether
        # flatly absent or an empty explicit source, cannot back a gate.
        absent_vars = sorted(
            v
            for v, rv in resolved.items()
            if rv.strategy == "absent" or rv.shape == "empty" or not rv.value
        )
        if absent_vars:
            return (
                f"Ungradable gate: this evaluator has gate (hard pass/fail) checklist "
                f"items, but variable(s) {', '.join(absent_vars)} resolved to nothing "
                "for this sample, so any gate verdict is weakly grounded. This "
                "usually means a binding points at data this sample doesn't carry."
            )

    scope = (evaluator.scope or "sample").strip()
    if scope not in _EVIDENCE_SCOPES:
        return None
    evidence = [e for e in mapping if (e.get("source") or "output") in _EVIDENCE_SOURCES]
    if not evidence:
        return None
    resolved_values = resolve_variables(unit, evidence)
    if resolved_values and all(_is_blank_resolution(v) for v in resolved_values.values()):
        surfaced = ", ".join(sorted({str(e.get("source") or "output") for e in evidence}))
        return (
            f"Insufficient evidence: this '{scope}'-scoped evaluator resolved its "
            f"evidence ({surfaced}) to empty, so the score is weakly grounded (a "
            "trace with no tool calls is not 'perfect tool discipline'). This "
            "usually means the trajectory could not be reconstructed for this sample."
        )
    return None


def with_resolution(draft: ScoreDraft, resolved: dict) -> ScoreDraft:
    """Per-variable resolution provenance, for debugging a score."""
    draft.sub_scores = list(draft.sub_scores) + [
        {
            "_resolution": {
                var: {"strategy": rv.strategy, "shape": rv.shape} for var, rv in resolved.items()
            }
        }
    ]
    return draft


def mark_low_provenance(draft: ScoreDraft, reason: str) -> ScoreDraft:
    """The score still stands but carries a structured ``_provenance`` marker
    plus a reasoning prefix the UI surfaces as a warning."""
    draft.sub_scores = list(draft.sub_scores) + [
        {"_provenance": {"level": "low", "reason": reason}}
    ]
    draft.reasoning = f"⚠ Low provenance — {reason}\n\n{draft.reasoning or ''}".strip()
    return draft


def judge_module(ctx: dict[str, Any] | None = None):
    """The judge implementation the calling system owns. The only place the two
    are chosen between, so a nested caller (dag leaf, trajectory in judge mode)
    cannot land on the other system's scoring by accident."""
    from overbae.services.eval.evaluators import gen_judge, judge

    if (ctx or {}).get("eval_surface") == SURFACE_GENERATIVE:
        return gen_judge
    return judge


def _evaluator_not_applicable(unit: EvalUnit, evaluator) -> ScoreDraft | None:
    """``config["applies_when"]`` gates the whole evaluator; no family runs when it fails."""
    pred = (getattr(evaluator, "config", None) or {}).get("applies_when")
    if not pred:
        return None
    trajectory = unit.trajectory or {}
    applies, reason = predicates.predicate_applies(
        pred, trajectory.get("runtime") or {}, trajectory
    )
    if applies:
        return None
    return ScoreDraft(
        name=evaluator.name,
        data_type=getattr(evaluator, "score_type", "numeric"),
        value=None,
        outcome=OUTCOME_NOT_APPLICABLE,
        reasoning=reason,
        scope=getattr(evaluator, "scope", "sample"),
        sub_scores=[{"_applies_when": {"predicate": pred, "reason": reason}}],
    )


_INDEX_CITE_RE = re.compile(r"\bindex(?:es)?\s+(\d+)\b", re.I)
# Not `input`: that word is the bound prompt (`{input}` / "the input context"),
# not a DOM widget. Browser `input` tools still match via has_clickish / index.
_ACTION_CITE_RE = re.compile(r"\b(clicks?|clicked|clicking|typed)\b", re.I)


def _nested_ints(value: Any) -> set[int]:
    found: set[int] = set()
    if isinstance(value, bool):
        return found
    if isinstance(value, int):
        found.add(value)
        return found
    if isinstance(value, dict):
        for inner in value.values():
            found.update(_nested_ints(inner))
        return found
    if isinstance(value, (list, tuple)):
        for inner in value:
            found.update(_nested_ints(inner))
    return found


def unit_tool_names_and_indices(unit: EvalUnit) -> tuple[set[str], set[int]]:
    names: set[str] = set()
    indices: set[int] = set()
    for node in ((unit.structured or {}).get("tool_graph") or {}).get("nodes") or []:
        names.add(str(node.get("tool") or "").lower())
        indices.update(_nested_ints(node.get("arguments")))
    return names, indices


def scope_draft_to_unit_tree(draft: ScoreDraft, unit: EvalUnit) -> ScoreDraft:
    """Judges read earlier turns in message history and fail this unit for
    those actions; a fail citing an action this tree never performed is dropped."""
    if draft.outcome != OUTCOME_SCORED:
        return draft
    failed = draft.passed is False or (
        draft.passed is None and isinstance(draft.value, (int, float)) and float(draft.value) == 0.0
    )
    if not failed:
        return draft
    text = draft.reasoning or ""
    if not (_ACTION_CITE_RE.search(text) or _INDEX_CITE_RE.search(text)):
        return draft
    names, indices = unit_tool_names_and_indices(unit)
    cited = {int(match) for match in _INDEX_CITE_RE.findall(text)}
    if cited and cited & indices:
        return draft
    has_clickish = any(
        "click" in name or name.endswith(":input") or name == "input" for name in names
    )
    if cited or not has_clickish:
        return ScoreDraft(
            name=draft.name,
            data_type=draft.data_type,
            value=None,
            passed=None,
            outcome=OUTCOME_NOT_APPLICABLE,
            reasoning=(
                "Cited action is not in this unit's span tree; earlier-turn "
                "clicks in message history are not gradable here."
            ),
            scope=draft.scope,
            sub_scores=draft.sub_scores,
            cost=draft.cost,
            latency_ms=draft.latency_ms,
            judge_trace_id=draft.judge_trace_id,
        )
    return draft


def scope_entry_to_unit_tree(entry: dict[str, Any], unit: EvalUnit) -> dict[str, Any]:
    scoped = scope_draft_to_unit_tree(
        ScoreDraft(
            name=str(entry.get("display_name") or entry.get("evaluator") or ""),
            value=entry.get("score") if isinstance(entry.get("score"), (int, float)) else None,
            passed=entry.get("passed") if isinstance(entry.get("passed"), bool) else None,
            outcome=str(entry.get("outcome") or OUTCOME_SCORED),
            reasoning=str(entry.get("rationale") or ""),
        ),
        unit,
    )
    if scoped.outcome == str(entry.get("outcome") or OUTCOME_SCORED) and scoped.reasoning == str(
        entry.get("rationale") or ""
    ):
        return entry
    patched = dict(entry)
    patched["score"] = scoped.value
    patched["passed"] = scoped.passed
    patched["outcome"] = scoped.outcome
    patched["rationale"] = scoped.reasoning
    return patched


_PRIMARY_BIND_SOURCES = frozenset({"output", "final_output", "reference"})


def _whole_blob_sources(mapping: list) -> set[str]:
    """Sources also bound as the whole blob (empty path or ``$``)."""
    sources: set[str] = set()
    for entry in mapping:
        if not isinstance(entry, dict):
            continue
        path = str(entry.get("jsonpath") or "").strip()
        if path in ("", "$"):
            sources.add(str(entry.get("source") or "output"))
    return sources


def _unresolved_binding_reason(unit: EvalUnit, evaluator) -> str | None:
    """A jsonpath that misses on a populated primary source would score a confident 0.

    Sidecar sources (metadata, tool_calls) and a miss next to a whole-blob
    binding of the same source stay gradable — the judge still has the text.
    """
    mapping = getattr(evaluator, "variable_mapping", None) or []
    if not mapping:
        return None
    resolved = resolve_variables_detailed(unit, mapping)
    whole = _whole_blob_sources(mapping)
    missed: list[str] = []
    for entry in mapping:
        if not isinstance(entry, dict):
            continue
        jsonpath = str(entry.get("jsonpath") or "")
        if not jsonpath or jsonpath.strip() == "$":
            continue
        var = str(entry.get("var") or "")
        source = str(entry.get("source") or "output")
        if source not in _PRIMARY_BIND_SOURCES or source in whole:
            continue
        rv = resolved.get(var)
        empty = (
            rv is None
            or rv.strategy == "absent"
            or rv.shape == "empty"
            or not str(rv.value or "").strip()
        )
        if not empty:
            continue
        if _is_nonempty(_maybe_parse(_source_object(unit, source))):
            missed.append(var or jsonpath)
    if not missed:
        return None
    return (
        f"not applicable: binding(s) {', '.join(missed)} resolved to nothing on a populated source"
    )


def evaluate(unit: EvalUnit, evaluator, ctx: dict[str, Any] | None = None) -> list[ScoreDraft]:
    """Statistical evaluators are dataset-scoped and belong to the task layer's
    aggregation pass, not here."""
    from overbae.services.eval.evaluators import deterministic, judge, trajectory

    ctx = ctx or {}
    not_applicable = _evaluator_not_applicable(unit, evaluator)
    if not_applicable is not None:
        return [not_applicable]
    unresolved = _unresolved_binding_reason(unit, evaluator)
    if unresolved:
        return [
            ScoreDraft(
                name=evaluator.name,
                data_type=getattr(evaluator, "score_type", "numeric"),
                value=None,
                outcome=OUTCOME_NOT_APPLICABLE,
                reasoning=unresolved,
                scope=getattr(evaluator, "scope", "sample"),
            )
        ]
    if (getattr(evaluator, "config", None) or {}).get("per_turn_judge"):
        from overbae.services.eval import per_turn_judge

        drafts = per_turn_judge.evaluate(unit, evaluator, ctx)
    else:
        kind = evaluator.kind
        if kind == "deterministic":
            drafts = deterministic.evaluate(unit, evaluator, ctx)
        elif kind == "trajectory":
            drafts = trajectory.evaluate(unit, evaluator, ctx)
        elif kind in ("llm_judge", "agentic"):
            drafts = judge_module(ctx).evaluate(unit, evaluator, ctx)
        elif kind == "statistical":
            # Emits the raw prediction for run-level aggregation.
            drafts = judge.emit_prediction(unit, evaluator)
        else:
            logger.warning("Unknown evaluator kind: %s", kind)
            drafts = []
    # Carved-unit click-leak: generate-eval scores a dataset row, not a span tree.
    if (ctx or {}).get("eval_surface") == SURFACE_GENERATIVE:
        return drafts
    return [scope_draft_to_unit_tree(draft, unit) for draft in drafts]
