"""Creation-time binding dry-run through the exact grade-time resolver.
A miss on a populated source is not-applicable at grade time, not a
capability-wide archive. Repairs are proposals, never applied silently.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from typing import Any

from overbae.services.eval.evaluators.base import (
    _GROUNDING_VAR_KEYS,
    EvalUnit,
    ResolvedVariable,
    _infer_source,
    _is_nonempty,
    _maybe_parse,
    _normalize_var,
    _source_object,
    contract_from_capability,
    resolve_jsonpath,
    resolve_one,
    resolve_variables_detailed,
)
from overbae.services.eval.evidence import GENERATE

logger = logging.getLogger(__name__)

GREEN = "green"
AMBER = "amber"
RED = "red"
UNKNOWN = "unknown"
# Reads model output the creation-time units lack; generate mode populates it
# at grade time, so the wizard must not red-flag it.
SKIPPED = "skipped"

REPAIR_AUTO = "auto"
REPAIR_REGENERATE = "regenerate"

_AUTO_ACTIONS = frozenset({"set_source", "strip_wrapper", "set_jsonpath", "resource_grounding"})

# Lexical variants only: ``rows`` must NEVER fuzzy-match ``total_rows`` — that
# per-sample-vs-dataset conflation is what auto-repair must refuse.
_SAFE_LEAF_SUFFIXES = ("_exact", "_count", "_total", "_value", "_num")

# ``output``/``final_output`` are excluded: they carry stringified JSON the
# resolver parses, so a jsonpath into them is legitimate.
_TEXT_ONLY_SOURCES = frozenset({"input", "last_user_input", "all_user_messages", "conversation"})

# Resolved by guessing: worked on this data by luck, so amber.
_FUZZY_STRATEGIES = frozenset({"default", "semantic_ambiguous"})

DEFAULT_SAMPLE_LIMIT = 3


@dataclass
class VarHealth:
    var: str
    source: str
    jsonpath: str
    ok_units: int
    total_units: int
    strategies: list[str] = field(default_factory=list)
    shapes: list[str] = field(default_factory=list)
    status: str = GREEN  # green | amber | skipped | red
    fix_hint: str = ""
    proposed_repair: dict[str, Any] | None = None
    repair_kind: str = ""  # "" | auto | regenerate

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BindingHealth:
    status: str  # green | amber | red | unknown
    checked_units: int
    variables: list[VarHealth] = field(default_factory=list)
    reason: str = ""

    @property
    def failing(self) -> list[VarHealth]:
        return [v for v in self.variables if v.status == RED]

    @property
    def weak(self) -> list[VarHealth]:
        return [v for v in self.variables if v.status == AMBER]

    @property
    def skipped(self) -> list[VarHealth]:
        return [v for v in self.variables if v.status == SKIPPED]

    @property
    def needs_regeneration(self) -> list[VarHealth]:
        return [v for v in self.variables if v.repair_kind == REPAIR_REGENERATE]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "checked_units": self.checked_units,
            "reason": self.reason,
            "variables": [v.to_dict() for v in self.variables],
            "failing_vars": [v.var for v in self.failing],
            "skipped_vars": [v.var for v in self.skipped],
            "needs_regeneration_vars": [v.var for v in self.needs_regeneration],
        }


def _resolution_ok(rv: ResolvedVariable) -> bool:
    return rv.strategy != "absent" and rv.shape != "empty"


def _entry_fields(entry: Any) -> tuple[str, str, str]:
    if hasattr(entry, "model_dump"):
        entry = entry.model_dump()
    var = str(entry.get("var") or "")
    source = str(entry.get("source") or "")
    jsonpath = str(entry.get("jsonpath") or "")
    return var, source, jsonpath


def _mapping_of(spec: Any) -> list[dict[str, Any]]:
    raw = getattr(spec, "variable_mapping", None)
    if raw is None and isinstance(spec, dict):
        raw = spec.get("variable_mapping")
    out: list[dict[str, Any]] = []
    for entry in raw or []:
        var, source, jsonpath = _entry_fields(entry)
        if not var:
            continue
        out.append({"var": var, "source": source, "jsonpath": jsonpath})
    return out


def _strip_wrapper(jsonpath: str) -> str:
    """``$.a.b`` -> ``$.b``."""
    body = jsonpath.strip()
    if body.startswith("$"):
        body = body[1:]
    body = body.lstrip(".")
    parts = body.split(".", 1)
    if len(parts) != 2:
        return ""
    return f"$.{parts[1]}"


def _is_output_derived(source: str, var: str) -> bool:
    """Empty on datapoint-derived units but populated by a generate-mode run, so
    an empty dry-run is unknown, not a failure."""
    norm_source = _normalize_var(source)
    if norm_source in ("output", "final_output", "structured"):
        return True
    if norm_source:
        return False
    return _infer_source(_normalize_var(var)) == "final_output"


def _base_object(unit: EvalUnit, source: str, var: str) -> Any:
    effective = source or _infer_source(_normalize_var(var))
    return _source_object(unit, effective)


def _jsonpath_keys(jsonpath: str) -> list[str]:
    """``$.a.b['c']`` -> ``['a', 'b', 'c']``."""
    return re.findall(r"[A-Za-z_][\w]*", jsonpath or "")


def _leaf_targets(jsonpath: str) -> set[str]:
    """``total_rows_exact`` -> ``{total_rows_exact, total_rows}``; never a
    semantic broadening."""
    keys = _jsonpath_keys(jsonpath)
    if not keys:
        return set()
    leaf = _normalize_var(keys[-1])
    targets = {leaf}
    for suffix in _SAFE_LEAF_SUFFIXES:
        if leaf.endswith(suffix) and len(leaf) > len(suffix):
            targets.add(leaf[: -len(suffix)])
    return targets


def _find_value_path(obj: Any, targets: set[str], prefix: str = "$") -> str | None:
    """Shallowest-first jsonpath to a non-empty value under one of *targets*."""
    obj = _maybe_parse(obj)
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{prefix}.{key}"
            if _normalize_var(str(key)) in targets and _is_nonempty(value):
                return here
        for key, value in obj.items():
            found = _find_value_path(value, targets, f"{prefix}.{key}")
            if found:
                return found
    elif isinstance(obj, list):
        for i, value in enumerate(obj):
            found = _find_value_path(value, targets, f"{prefix}[{i}]")
            if found:
                return found
    return None


def _propose_grounding_resource(
    unit: EvalUnit, var: str, source: str, jsonpath: str
) -> dict[str, Any] | None:
    """Only a jsonpath ROOTED at a grounding key is re-sourced; a per-sample
    field like ``$.summary.rows`` must never be re-sourced to a dataset-level
    ``total_rows``."""
    ctx = unit.reference_context or {}
    if not ctx or not jsonpath:
        return None
    keys = _jsonpath_keys(jsonpath)
    if len(keys) < 2:
        return None
    grounding_key = _GROUNDING_VAR_KEYS.get(_normalize_var(keys[0]), _normalize_var(keys[0]))
    grounding_value = ctx.get(grounding_key)
    if not _is_nonempty(grounding_value):
        return None
    targets = _leaf_targets(jsonpath)
    sub_path = _find_value_path(grounding_value, targets)
    if not sub_path:
        return None
    full_path = f"$.{grounding_key}{sub_path[1:]}"  # sub_path begins with "$"
    resolved = resolve_one(unit, var, source="reference", jsonpath=full_path)
    if resolved.strategy == "absent" or resolved.shape == "empty":
        return None
    return {
        "action": "resource_grounding",
        "set": {"source": "reference", "jsonpath": full_path},
        "detail": (
            f"'{var}' can't resolve from source '{source or 'inferred'}'"
            + (f"/jsonpath '{jsonpath}'" if jsonpath else "")
            + f", but the same value is collected grounding at reference "
            f"'{full_path}'. Re-source it there."
        ),
    }


def _propose_repair(unit: EvalUnit, var: str, source: str, jsonpath: str) -> dict[str, Any] | None:
    """A suggestion only, never an applied change."""
    norm = _normalize_var(var)

    grounding_resource = _propose_grounding_resource(unit, var, source, jsonpath)
    if grounding_resource is not None:
        return grounding_resource

    # A grounding var sourced to input/output never reaches the grounding tier.
    if source in ("input", "output", "final_output") and norm in _GROUNDING_VAR_KEYS:
        return {
            "action": "set_source",
            "set": {"source": "reference"},
            "detail": (
                f"'{var}' looks like collected grounding evidence but source "
                f"'{source}' never consults grounding; bind it to 'reference'."
            ),
        }

    if jsonpath:
        base_obj = _maybe_parse(_source_object(unit, source or "output"))
        stripped = _strip_wrapper(jsonpath)
        if stripped:
            matches = resolve_jsonpath(base_obj, stripped)
            if matches and any(m not in (None, "", [], {}) for m in matches):
                return {
                    "action": "strip_wrapper",
                    "set": {"jsonpath": stripped},
                    "detail": (
                        f"jsonpath '{jsonpath}' assumes a wrapper key that the data "
                        f"doesn't have; '{stripped}' resolves directly."
                    ),
                }

    if jsonpath and source in _TEXT_ONLY_SOURCES:
        return {
            "action": "review",
            "set": {},
            "detail": (
                f"source '{source}' resolves to conversation text; jsonpath "
                f"'{jsonpath}' cannot traverse a string. Pick a structured source "
                "(output/structured/reference) or drop the path."
            ),
        }

    return None


def _classify_var(
    unit_sample: EvalUnit,
    var: str,
    source: str,
    jsonpath: str,
    strategies: list[str],
    shapes: list[str],
    ok: int,
    total: int,
    *,
    mode: str | None = None,
    base_populated: bool = True,
) -> VarHealth:
    if ok == 0:
        # RED only when it would fail regardless of mode: a text source with a
        # jsonpath, a missing key in a POPULATED output, a wrong source class.
        if mode == GENERATE and _is_output_derived(source, var) and not base_populated:
            status = SKIPPED
        else:
            status = RED
    elif ok < total or all(s in _FUZZY_STRATEGIES for s in strategies):
        status = AMBER
    else:
        status = GREEN

    health = VarHealth(
        var=var,
        source=source,
        jsonpath=jsonpath,
        ok_units=ok,
        total_units=total,
        strategies=strategies,
        shapes=shapes,
        status=status,
    )
    if status == SKIPPED:
        health.fix_hint = (
            f"'{var}' reads the model's output, which generate-mode runs produce "
            "at grade time; the dataset has no captured run to dry-run against yet, "
            "so this binding is unverified (not failing)."
        )
        return health
    if status != GREEN:
        proposal = _propose_repair(unit_sample, var, source, jsonpath)
        if proposal is not None:
            health.proposed_repair = proposal
            health.fix_hint = proposal["detail"]
            if proposal.get("action") in _AUTO_ACTIONS:
                health.repair_kind = REPAIR_AUTO
            elif status == RED:
                health.repair_kind = REPAIR_REGENERATE
        elif status == RED:
            health.repair_kind = REPAIR_REGENERATE
            health.fix_hint = (
                f"'{var}' (source='{source}'"
                + (f", jsonpath='{jsonpath}'" if jsonpath else "")
                + ") resolved to nothing on every sample and has no safe automatic "
                "fix — regenerate or re-author this evaluator against data the "
                "dataset actually carries."
            )
    return health


def dry_run_spec(spec: Any, units: list[EvalUnit], *, mode: str | None = None) -> BindingHealth:
    mapping = _mapping_of(spec)
    if not units:
        return BindingHealth(UNKNOWN, 0, [], reason="no sample units available to dry-run")
    if not mapping:
        # Deterministic/statistical evals read the whole output via config.
        return BindingHealth(GREEN, len(units), [])

    per_var_strategies: dict[str, list[str]] = {e["var"]: [] for e in mapping}
    per_var_shapes: dict[str, list[str]] = {e["var"]: [] for e in mapping}
    per_var_ok: dict[str, int] = {e["var"]: 0 for e in mapping}
    # "Output empty" (generate-mode unknown) vs "output present, path missed" (red).
    per_var_base_populated: dict[str, bool] = {e["var"]: False for e in mapping}

    for unit in units:
        resolved = resolve_variables_detailed(unit, mapping)
        for entry in mapping:
            if _is_nonempty(_maybe_parse(_base_object(unit, entry["source"], entry["var"]))):
                per_var_base_populated[entry["var"]] = True
            rv = resolved.get(entry["var"])
            if rv is None:
                per_var_strategies[entry["var"]].append("absent")
                per_var_shapes[entry["var"]].append("empty")
                continue
            per_var_strategies[entry["var"]].append(rv.strategy)
            per_var_shapes[entry["var"]].append(rv.shape)
            if _resolution_ok(rv):
                per_var_ok[entry["var"]] += 1

    sample_unit = units[0]
    variables = [
        _classify_var(
            sample_unit,
            entry["var"],
            entry["source"],
            entry["jsonpath"],
            per_var_strategies[entry["var"]],
            per_var_shapes[entry["var"]],
            per_var_ok[entry["var"]],
            len(units),
            mode=mode,
            base_populated=per_var_base_populated[entry["var"]],
        )
        for entry in mapping
    ]

    if any(v.status == RED for v in variables):
        status = RED
    elif any(v.status == AMBER for v in variables):
        status = AMBER
    elif any(v.status == SKIPPED for v in variables):
        status = SKIPPED
    else:
        status = GREEN
    return BindingHealth(status, len(units), variables)


def dry_run_bindings(
    specs: list[Any], units: list[EvalUnit], *, mode: str | None = None
) -> dict[str, BindingHealth]:
    return {
        getattr(spec, "name", _spec_name(spec)): dry_run_spec(spec, units, mode=mode)
        for spec in specs
    }


def _spec_name(spec: Any) -> str:
    if isinstance(spec, dict):
        return str(spec.get("name") or "")
    return str(getattr(spec, "name", ""))


def sample_units_for_dataset(
    dataset, *, limit: int = DEFAULT_SAMPLE_LIMIT, mode: str | None = None
) -> list[EvalUnit]:
    """``generate`` mode prefers a recent generate run's regenerated trajectory,
    which carries the real ``final_output``; otherwise the dataset rows
    normalized exactly as run assembly does. ``[]`` means skip the check."""
    from overbae.services.eval import normalizer  # noqa: PLC0415 — avoid import cycle
    from overbae.services.eval.grounding import build_reference_context  # noqa: PLC0415
    from overbae.tasks.eval import normalize_datapoint  # noqa: PLC0415 — task layer

    capability = getattr(dataset, "capability", None)
    output_fields, output_schema, input_schema = contract_from_capability(capability)
    reference_context = build_reference_context(dataset)

    if mode == GENERATE:
        run_units = _units_from_recent_generate_run(
            dataset, limit, output_fields, output_schema, reference_context, input_schema
        )
        if run_units:
            return run_units

    from overbae.services.datasets.rows import sample_rows  # noqa: PLC0415

    try:
        datapoints = sample_rows(dataset, limit)
    except Exception as exc:  # noqa: BLE001 — additive check, never fatal
        logger.warning("binding dry-run: row fetch failed for %s: %s", dataset, exc)
        return []
    if not datapoints:
        return []

    units: list[EvalUnit] = []
    for dp in datapoints:
        try:
            normalized = normalize_datapoint(dp)
            structured = normalizer.structure_trajectory(normalized)
        except Exception as exc:  # noqa: BLE001 — one bad row never sinks the check
            logger.warning("binding dry-run: normalize failed for datapoint %s: %s", dp, exc)
            continue
        units.append(
            EvalUnit(
                trajectory=normalized,
                structured=structured,
                expected=normalized.get("expected"),
                sample_id=str(getattr(dp, "id", "")),
                output_fields=output_fields,
                output_schema=output_schema,
                input_schema=input_schema,
                reference_context=reference_context,
            )
        )
    return units


_SYNTHETIC_TYPE_VALUES: dict[str, Any] = {
    "string": "synthetic",
    "number": 0.5,
    "integer": 1,
    "boolean": True,
    "array": [],
    "object": {},
}

_SYNTHETIC_PATTERN_VALUES = (
    (r"^\d{4}-\d{2}-\d{2}$", "2026-01-01"),
    (r"^\d{4}-\d{2}-\d{2}[T ]", "2026-01-01T00:00:00Z"),
)


def _synthetic_value(rules: dict[str, Any]) -> Any:
    enum = rules.get("enum")
    if enum:
        return enum[0]
    pattern = rules.get("pattern")
    for known, value in _SYNTHETIC_PATTERN_VALUES:
        if pattern == known:
            return value
    lo, hi = rules.get("min"), rules.get("max")
    if lo is not None or hi is not None:
        lo = lo if lo is not None else (hi if hi is not None else 0)
        return lo
    return _SYNTHETIC_TYPE_VALUES.get(rules.get("type") or "string", "synthetic")


def synthetic_row_from_card(card: dict[str, Any] | None) -> dict[str, Any]:
    from overbae.services.eval.card_compiler import (  # noqa: PLC0415 — avoid cycle
        _schema_field_rules,
        card_output_field_names,
    )

    card = card if isinstance(card, dict) else {}
    properties = (card.get("output_schema") or {}).get("properties") or {}
    properties = properties if isinstance(properties, dict) else {}
    row: dict[str, Any] = {}
    for name in card_output_field_names(card):
        row[name] = _synthetic_value(_schema_field_rules(properties.get(name)))
    return row


def validate_specs_on_synthetic_row(
    specs: list[Any], card: dict[str, Any] | None
) -> list[dict[str, str]]:
    """Failure records for specs that crash or are never-passable (bad regex,
    unknown check) against one synthetic card-schema row."""
    import json  # noqa: PLC0415
    from types import SimpleNamespace  # noqa: PLC0415

    from overbae.services.eval import normalizer  # noqa: PLC0415 — avoid import cycle
    from overbae.services.eval.evaluators import deterministic, gen_judge  # noqa: PLC0415
    from overbae.services.eval.rubric_compiler import (  # noqa: PLC0415
        build_checklist_prompt,
        build_claims_prompt,
        build_judge_prompt,
    )

    row = synthetic_row_from_card(card)
    trajectory = {"input": "synthetic validation input", "final_output": json.dumps(row)}
    try:
        structured = normalizer.structure_trajectory(trajectory)
    except Exception:  # noqa: BLE001 — synthetic structure is best-effort
        structured = {}
    unit = EvalUnit(trajectory=trajectory, structured=structured, sample_id="synthetic")

    failures: list[dict[str, str]] = []
    for spec in specs:
        name = _spec_name(spec)
        kind = str(
            getattr(spec, "kind", "") or (spec.get("kind") if isinstance(spec, dict) else "")
        )
        ns = SimpleNamespace(
            name=name,
            kind=kind,
            scope=str(getattr(spec, "scope", "final_output")),
            score_type=str(getattr(spec, "score_type", "numeric")),
            score_min=float(getattr(spec, "score_min", 0.0)),
            score_max=float(getattr(spec, "score_max", 1.0)),
            pass_threshold=getattr(spec, "pass_threshold", None),
            config=dict(getattr(spec, "config", None) or {}),
            rubric_md=str(getattr(spec, "rubric_md", "")),
            checklist=[
                item if isinstance(item, dict) else item.model_dump(exclude_none=True)
                for item in getattr(spec, "checklist", None) or []
            ],
            choices=[],
            variable_mapping=list(getattr(spec, "variable_mapping", None) or []),
        )
        try:
            if kind == "deterministic":
                drafts = deterministic.evaluate(unit, ns, {})
                reason = (drafts[0].reasoning or "") if drafts else ""
                if reason.startswith("Unknown check") or reason.startswith("bad regex"):
                    failures.append({"name": name, "stage": "synthetic_row", "reason": reason})
            elif kind in ("llm_judge", "agentic"):
                placeholders = {
                    str(e.get("var") if isinstance(e, dict) else e.var): "<synthetic>"
                    for e in getattr(spec, "variable_mapping", None) or []
                }
                if gen_judge.is_proportional(ns):
                    build_claims_prompt(ns, placeholders)
                elif ns.checklist:
                    build_checklist_prompt(ns, placeholders)
                else:
                    build_judge_prompt(ns, placeholders)
        except Exception as exc:  # noqa: BLE001 — a crashing spec is exactly the catch
            failures.append(
                {"name": name, "stage": "synthetic_row", "reason": f"{type(exc).__name__}: {exc}"}
            )
    return failures


SWEEP_QUARANTINE_PREFIX = "first-data binding sweep:"


def restore_sweep_quarantined(*, capability=None) -> int:
    """Undo capability-wide archives that the first-data sweep used to write."""
    from overbae.models import Evaluator  # noqa: PLC0415

    qs = Evaluator.objects.filter(
        is_archived=True,
        config__quarantined_reason__startswith=SWEEP_QUARANTINE_PREFIX,
    )
    if capability is not None:
        qs = qs.filter(capability=capability)
    restored = 0
    for evaluator in qs:
        cfg = dict(evaluator.config or {})
        cfg.pop("quarantined_reason", None)
        evaluator.config = cfg
        evaluator.is_archived = False
        evaluator.save(update_fields=["config", "is_archived"])
        restored += 1
    return restored


def sweep_dataset_bindings(dataset) -> dict[str, Any]:
    """Record binding health for this dataset's rows. A miss on this shape is
    not-applicable at grade time, not a capability-wide archive."""
    from overbae.models import Evaluator  # noqa: PLC0415
    from overbae.services.eval.specs import AUTHORED_GENERATORS  # noqa: PLC0415

    capability = getattr(dataset, "capability", None)
    restored = restore_sweep_quarantined(capability=capability) if capability is not None else 0
    units = sample_units_for_dataset(dataset, mode=GENERATE)
    if not units:
        return {"swept": 0, "quarantined": 0, "restored": restored, "skipped": "no rows"}

    if capability is None:
        return {"swept": 0, "quarantined": 0, "restored": restored, "skipped": "no capability"}
    evaluators = list(
        Evaluator.objects.filter(
            capability=capability,
            is_archived=False,
            config__provenance__generator__in=tuple(AUTHORED_GENERATORS),
        ).exclude(variable_mapping=[])
    )
    flagged = 0
    for evaluator in evaluators:
        health = dry_run_spec(evaluator, units, mode=GENERATE)
        cfg = dict(evaluator.config or {})
        cfg["binding_health"] = health.to_dict()
        evaluator.config = cfg
        evaluator.save(update_fields=["config"])
        if health.status != RED:
            continue
        flagged += 1
        logger.warning(
            "binding sweep: evaluator %s (%s) bindings miss this dataset — vars %s",
            evaluator.id,
            evaluator.name,
            [v.var for v in health.failing],
        )
    return {"swept": len(evaluators), "quarantined": 0, "flagged": flagged, "restored": restored}


def _units_from_recent_generate_run(
    dataset,
    limit: int,
    output_fields: dict[str, Any],
    output_schema: dict[str, Any],
    reference_context: dict[str, Any],
    input_schema: dict[str, Any],
) -> list[EvalUnit]:
    from overbae.models import EvalSample, EvalVariant  # noqa: PLC0415 — avoid import cycle

    try:
        samples = list(
            EvalSample.objects.filter(run__dataset=dataset, variant__mode=EvalVariant.Mode.GENERATE)
            .exclude(degraded=True)
            .order_by("-created_at")[: max(limit * 4, limit)]
        )
    except Exception as exc:  # noqa: BLE001 — additive check, never fatal
        logger.warning("binding dry-run: generate-sample fetch failed for %s: %s", dataset, exc)
        return []

    units: list[EvalUnit] = []
    for sample in samples:
        trajectory = sample.trajectory or {}
        if not str(trajectory.get("final_output") or "").strip():
            continue
        units.append(
            EvalUnit(
                trajectory=trajectory,
                structured=sample.structured or {},
                expected=sample.expected,
                sample_id=str(sample.id),
                output_fields=output_fields,
                output_schema=output_schema,
                input_schema=input_schema,
                reference_context=reference_context,
            )
        )
        if len(units) >= limit:
            break
    return units
