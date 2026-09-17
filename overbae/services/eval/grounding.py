"""Eval grounding resolver. Every hop is best-effort; a missing one never
fails the resolve."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from overbae.services.tool_names import canonical_tool_name

logger = logging.getLogger(__name__)


@dataclass
class EvalGroundingContext:
    dataset: Any = None
    # Set only on the capability-first path; the dataset path uses ``dataset.capability``.
    capability: Any = None
    dataset_card: dict[str, Any] | None = None
    data_version: str = ""
    codebase_card: dict[str, Any] | None = None
    codebase_commit: str = ""
    report: dict[str, Any] | None = None
    evaluator_inventory: list[Any] = field(default_factory=list)
    # Empty means prompt-agnostic authoring.
    prompt_text: str = ""
    prompt_id: str = ""
    prompt_label: str = ""


def resolve_grounding(dataset, *, prompt=None) -> EvalGroundingContext:
    """*prompt* is a ``Prompt``, its id, or ``None``; unresolvable leaves the
    context prompt-agnostic."""
    prompt_text, prompt_id, prompt_label = _resolve_prompt(prompt)
    return EvalGroundingContext(
        dataset=dataset,
        codebase_card=_capability_card(getattr(dataset, "capability", None)),
        evaluator_inventory=_resolve_evaluator_inventory(dataset),
        prompt_text=prompt_text,
        prompt_id=prompt_id,
        prompt_label=prompt_label,
    )


def example_dataset(capability) -> Any:
    """First non-empty dataset on the capability, for card-sync when no row is pinned."""
    if capability is None or getattr(capability, "pk", None) is None:
        return None
    for dataset in capability.datasets.order_by("-updated_at")[:10]:
        cell = dataset.active_cell
        if cell is not None and cell.rows > 0:
            return dataset
    return None


def attach_example_dataset(ctx: EvalGroundingContext) -> EvalGroundingContext:
    if ctx.dataset is not None:
        return ctx
    capability = ctx.capability
    dataset = example_dataset(capability)
    if dataset is not None:
        ctx.dataset = dataset
    return ctx


def resolve_grounding_for_capability(capability) -> EvalGroundingContext:
    metadata = getattr(capability, "improvement_metadata", None) or {}
    return EvalGroundingContext(
        dataset=None,
        capability=capability,
        codebase_card=_capability_card(capability),
        evaluator_inventory=_capability_evaluator_inventory(capability),
        prompt_text=str(metadata.get("system_prompt") or ""),
    )


def _capability_card(capability) -> dict[str, Any] | None:
    card = (getattr(capability, "improvement_metadata", None) or {}).get("capability_card")
    return card if isinstance(card, dict) and card else None


def _resolve_evaluator_inventory(dataset) -> list[Any]:
    project_id = dataset.project_id or (
        dataset.capability.project_id if getattr(dataset, "capability_id", None) else None
    )
    return _library_inventory(project_id)


def _capability_evaluator_inventory(capability) -> list[Any]:
    return _library_inventory(getattr(capability, "project_id", None))


def _library_inventory(project_id) -> list[Any]:
    from overbae.models import Evaluator  # noqa: PLC0415 — avoid import cycle at module load

    if project_id is None:
        return []
    try:
        return list(Evaluator.objects.library_for_project(project_id))
    except Exception as exc:  # noqa: BLE001 — grounding is additive, never fatal
        logger.warning("grounding: evaluator inventory failed for project %s: %s", project_id, exc)
        return []


def _resolve_prompt(prompt) -> tuple[str, str, str]:
    if prompt is None:
        return "", "", ""
    from overbae.models import Prompt  # noqa: PLC0415 — avoid import cycle at module load

    obj = prompt
    if not isinstance(prompt, Prompt):
        try:
            obj = Prompt.objects.filter(id=str(prompt)).first()
        except Exception as exc:  # noqa: BLE001 — grounding is additive, never fatal
            logger.warning("grounding: prompt lookup failed for %r: %s", prompt, exc)
            return "", "", ""
    if obj is None:
        return "", "", ""
    label = obj.label or f"v{obj.version}"
    return obj.system_prompt or "", str(obj.id), label


# Mirrors the workshop ``dataset_facts.json`` the analysis capability reads at runtime.
_DATASET_FACTS_KEYS: tuple[str, ...] = (
    "format",
    "summary",
    "volume_and_tokens",
    "distribution",
    "schema",
    "label_space",
    "target_stats",
    "hygiene",
    "io_mapping",
    "reference",
)

# Seconds: a fresh bundle lands within a run; the fan-out resolves once per version.
_REFERENCE_CONTEXT_TTL = 600


def _workshop_facts_from_report(report: dict[str, Any] | None) -> dict[str, Any] | None:
    """Only v4 reports carry the kernel profile inline."""
    if isinstance(report, dict) and report.get("version") == 4:
        profile = report.get("profile")
        if isinstance(profile, dict) and profile:
            return {k: v for k, v in profile.items() if not str(k).startswith("_")}
    return None


def _extract_reference_context(grounding: EvalGroundingContext) -> dict[str, Any]:
    """A key appears only when the context carries it, so the resolver keeps
    abstain-on-absent."""
    card = grounding.dataset_card or {}
    codebase = grounding.codebase_card or {}
    report = grounding.report or {}
    out: dict[str, Any] = {}

    if card:
        out["dataset_card"] = card
        row_ids = ((card.get("provenance") or {}).get("row_ids")) or []
        if row_ids:
            out["known_row_ids"] = row_ids
        facts = {k: card.get(k) for k in _DATASET_FACTS_KEYS if card.get(k)}
        if facts:
            out["dataset_facts"] = facts

    # A trace-derived dataset can lack the card spine; curated card keys win on overlap.
    workshop_facts = _workshop_facts_from_report(grounding.report)
    if workshop_facts:
        out["dataset_facts"] = {**workshop_facts, **out.get("dataset_facts", {})}

    if report:
        out["workshop_report"] = report

    tool_spec = codebase.get("tool_spec") or []
    if tool_spec:
        out["tool_spec"] = tool_spec

    return out


def build_reference_context(dataset) -> dict[str, Any]:
    """Cached per ``(dataset_id, data_version)``; any failure yields an empty map."""
    from django.core.cache import cache  # noqa: PLC0415 — avoid import at module load

    product = getattr(dataset, "active_cell", None)
    version = product.fingerprint if product is not None else ""
    cache_key = f"eval_ref_ctx:{getattr(dataset, 'id', '')}:{version}"
    cached = cache.get(cache_key)
    if cached is not None:
        return cached

    try:
        ctx = _extract_reference_context(resolve_grounding(dataset))
    except Exception as exc:  # noqa: BLE001 — grounding is additive, never fatal
        logger.warning(
            "reference context resolve failed for %s: %s", getattr(dataset, "id", "?"), exc
        )
        ctx = {}
    cache.set(cache_key, ctx, timeout=_REFERENCE_CONTEXT_TTL)
    return ctx


def _lines(items: list[str], prefix: str = "- ") -> str:
    return "\n".join(f"{prefix}{item}" for item in items)


def _indexed_lines(items: list[str]) -> str:
    return "\n".join(f"- [{i}] {item}" for i, item in enumerate(items))


def _dataset_section(card: dict[str, Any], data_version: str) -> str:
    lines: list[str] = [f"### Dataset capability card (data_version {data_version or 'unknown'})"]
    fmt = card.get("format") or "unknown"
    rows = (card.get("volume_and_tokens") or {}).get("total_rows") or 0
    lines.append(f"Format: {fmt} · {rows} rows")
    summary = (card.get("summary") or "").strip()
    if summary:
        lines.append(f"Summary: {summary}")
    reference = card.get("reference") or {}
    if reference.get("description"):
        cols = ", ".join(reference.get("columns") or [])
        lines.append(f"Reference semantics ({cols}): {reference['description']}")
    signals = [
        f"{qs.get('signal')} (severity={qs.get('severity', 'weighted')})"
        for qs in card.get("quality_signals") or []
        if qs.get("signal")
    ]
    if signals:
        lines.append("Quality signals (lift these VERBATIM into checklist items):")
        lines.append(_lines(signals))
    modes = [
        fm.get("description") for fm in card.get("failure_modes") or [] if fm.get("description")
    ]
    if modes:
        lines.append("Known failure modes:")
        lines.append(_lines(modes))
    label_space = card.get("label_space") or {}
    if label_space.get("balance") == "skewed":
        lines.append(
            f"Label-space caveat: column {label_space.get('column')!r} is skewed/degenerate."
        )
    return "\n".join(lines)


def _codebase_section(card: dict[str, Any], commit: str) -> str:
    lines: list[str] = [f"### Capability capability card (commit {commit[:12] or 'unknown'})"]
    if card.get("task"):
        lines.append(f"Task: {card['task']}")
    expected = card.get("expected_output") or {}
    if isinstance(expected, dict) and expected.get("description"):
        lines.append(f"What a good output looks like: {expected['description']}")
    criteria = [str(c) for c in card.get("success_criteria") or [] if str(c).strip()]
    if criteria:
        lines.append(
            "Success criteria (lift these VERBATIM into checklist items; "
            "cite as codebase_card.success_criteria[i]):"
        )
        lines.append(_indexed_lines(criteria))
    eo_signals = (
        [str(s) for s in expected.get("quality_signals") or [] if str(s).strip()]
        if isinstance(expected, dict)
        else []
    )
    if eo_signals:
        # The citation path must name expected_output or provenance drifts.
        lines.append(
            "Expected-output quality signals "
            "(cite as codebase_card.expected_output.quality_signals[i]):"
        )
        lines.append(_indexed_lines(eo_signals))
    modes = [str(m) for m in card.get("failure_modes") or [] if str(m).strip()]
    if modes:
        lines.append(
            "Known failure modes (author judges that verify the output avoids these; "
            "cite as codebase_card.failure_modes[i]):"
        )
        lines.append(_indexed_lines(modes))
    tool_names = [
        str(t.get("name")).strip()
        for t in card.get("tool_spec") or []
        if isinstance(t, dict) and str(t.get("name") or "").strip()
    ]
    if tool_names:
        lines.append(
            "Declared tools (tool_spec — the ONLY names observable as tool calls on a "
            f"trace): {', '.join(tool_names)}"
        )
    else:
        lines.append(
            "Declared tools (tool_spec): NONE — no tool calls are observable on this "
            "agent's traces; never require a tool call as evidence."
        )
    paths = [p for p in card.get("trajectory_map") or [] if isinstance(p, dict) and p.get("id")]
    if paths:
        lines.append(
            "Trajectory map — the agent's named execution paths, derived statically from its "
            "code (cite as codebase_card.trajectory_map[i]). Terminal kinds: emits_record = "
            "returns a real output record; returns_empty / escalates = a DECLARED refusal or "
            "handoff, not a broken output (an empty deliverable — '', [], {}, null — IS this "
            "terminal); error_exit = the path fails. A path's `tools`/`sequence` entries name "
            "internal callables; only the declared tool_spec names above are observable as "
            "tool calls:"
        )
        rendered = []
        for path in paths:
            terminal = path.get("terminal") if isinstance(path.get("terminal"), dict) else {}
            parts = [f"{path.get('id')} — routing: {path.get('routing') or 'unspecified'}"]
            sequence = [str(s) for s in path.get("sequence") or [] if str(s).strip()]
            if sequence:
                parts.append(f"sequence: {' -> '.join(sequence)}")
            tools = [str(t) for t in path.get("tools") or [] if str(t).strip()]
            if tools:
                observable = [t for t in tools if t in tool_names]
                internal = [t for t in tools if t not in tool_names]
                if observable:
                    parts.append(f"tools: {', '.join(observable)}")
                if internal:
                    parts.append(
                        f"internal callables (NOT observable as tool calls): {', '.join(internal)}"
                    )
            kind = str(terminal.get("kind") or "").strip()
            description = str(terminal.get("description") or "").strip()
            terminal_text = f"terminal: {kind or 'unknown'}" + (
                f" ({description})" if description else ""
            )
            # Judges embed this text verbatim, so the empty-shape equivalence
            # must travel with it.
            if kind in ("returns_empty", "escalates"):
                terminal_text += (
                    " — on a live trace ANY empty deliverable ('', [], {}, null) IS this terminal"
                )
            parts.append(terminal_text)
            divergences = [str(d) for d in path.get("divergences") or [] if str(d).strip()]
            if divergences:
                parts.append(f"diverges to: {', '.join(divergences)}")
            rendered.append("; ".join(parts))
        lines.append(_indexed_lines(rendered))
    fields = card.get("output_fields") or {}
    if fields:
        lines.append(
            "Output contract fields (the LIVE deliverable surface — what a trace's "
            f"final output carries): {', '.join(fields.keys())}"
        )
    schema = card.get("output_schema") or {}
    required = schema.get("required_keys") or []
    if required:
        layer = (
            "Required model-layer keys (validated inside the agent — NOT required on "
            "the live deliverable)"
            if fields
            else "Required output keys"
        )
        lines.append(f"{layer}: {', '.join(str(k) for k in required)}")
    props = schema.get("properties") or {}
    if isinstance(props, dict) and props:
        model_layer_note = (
            " — keys absent from the output contract fields above do NOT appear on the "
            "live deliverable"
            if fields
            else ""
        )
        lines.append(f"Output schema (internal model layer{model_layer_note}):")
        lines.append(_lines([f"{k}: {v}" for k, v in list(props.items())[:12]]))
    vocab = card.get("vocabulary") or {}
    if vocab:
        lines.append("Vocabulary:")
        lines.append(_lines([f"{k}: {v}" for k, v in list(vocab.items())[:10]]))
    return "\n".join(lines)


_PROMPT_SECTION_BUDGET = 2500


def _prompt_section(prompt_text: str, prompt_label: str = "") -> str:
    text = (prompt_text or "").strip()
    if not text:
        return ""
    if len(text) > _PROMPT_SECTION_BUDGET:
        text = text[:_PROMPT_SECTION_BUDGET].rstrip() + " …(truncated)"
    heading = "### Selected system prompt (the capability is instructed to…)"
    label = (prompt_label or "").strip()
    if label:
        heading += f" — {label}"
    return (
        f"{heading}\n"
        "Author/weight evals toward what THIS prompt instructs the capability to do "
        "(its constraints, required behaviors, and output expectations):\n"
        f"{text}"
    )


def _report_section(report: dict[str, Any]) -> str:
    lines: list[str] = ["### Workshop analysis report signals"]
    agenda = report.get("agenda_coverage") or {}
    uncovered = agenda.get("uncovered_intents") or []
    if uncovered:
        lines.append("Uncovered agenda intents (target evals at these):")
        lines.append(_lines([str(u) for u in uncovered]))
    issues = report.get("validation_issues") or []
    if issues:
        lines.append("Top validation issues:")
        rendered = []
        for issue in issues[:5]:
            if isinstance(issue, dict):
                text = issue.get("issue") or issue.get("description") or str(issue)
            else:
                text = str(issue)
            rendered.append(str(text)[:300])
        lines.append(_lines(rendered))
    compatibility = report.get("compatibility") or {}
    missing = compatibility.get("missing_for_eval") or []
    if missing:
        lines.append("Missing for eval-readiness:")
        lines.append(_lines([str(m) for m in missing]))
    readiness = report.get("eval_readiness") or {}
    cohorts = [
        f"{i.get('id') or i.get('fix') or 'issue'} ({len(i.get('row_signatures') or [])} rows)"
        for i in readiness.get("issues") or []
        if isinstance(i, dict)
    ]
    if cohorts:
        lines.append("Row cohorts from eval_readiness issues:")
        lines.append(_lines(cohorts))
    return "\n".join(lines)


def _parse_json_maybe(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    stripped = value.strip()
    if stripped[:1] in "{[":
        try:
            return json.loads(stripped)
        except (ValueError, TypeError):
            return value
    return value


_SOURCE_TYPE_TABLE = (
    "Bindable sources and the type each resolves to (a jsonpath only works on a "
    "structured type):\n"
    "- output / final_output → the model's answer (text, sometimes stringified JSON)\n"
    "- input / last_user_input / all_user_messages / conversation → plain TEXT "
    "(a jsonpath can NEVER traverse these — bind by source only)\n"
    "- reference / expected → ground truth (dict or string, dataset-dependent)\n"
    "- messages / trajectory / tool_calls / tool_definitions → lists\n"
    "- structured / metadata / sample → objects"
)


_REFERENCE_PATH_DEPTH = 3
_REFERENCE_PATH_CAP = 24


def _leaf_paths(obj: Any, prefix: str = "$", depth: int = _REFERENCE_PATH_DEPTH) -> list[tuple]:
    if depth <= 0 or not isinstance(obj, (dict, list)):
        return [(prefix, type(obj).__name__)]
    if isinstance(obj, dict):
        out: list[tuple] = []
        for key, value in obj.items():
            out.extend(_leaf_paths(value, f"{prefix}.{key}", depth - 1))
        return out or [(prefix, "dict")]
    return _leaf_paths(obj[0], f"{prefix}[0]", depth - 1) if obj else [(prefix, "list")]


def _surface_table_lines(expected: dict[str, Any], output_field_names: list[str]) -> list[str]:
    """Lets the author bind a nested gold value at its real leaf path."""
    lines = ["### Surface / shape table (output-schema key → observed reference path)"]
    ref_by_norm = {str(k).lower(): (str(k), v) for k, v in expected.items()}
    for name in output_field_names:
        match = ref_by_norm.get(name.lower())
        if match is None:
            lines.append(
                f"- {name} → no matching reference key observed "
                "(grade this field without a reference compare)"
            )
            continue
        key, value = match
        if isinstance(value, (dict, list)):
            leaves = ", ".join(path for path, _ in _leaf_paths(value, f"$.{key}")[:6])
            lines.append(
                f"- {name} → reference '$.{key}' is NESTED — bind the leaf you compare "
                f"(observed leaves: {leaves}), never the flat '$.{name}'"
            )
        else:
            lines.append(f"- {name} → reference '$.{key}' ({type(value).__name__})")
    return lines


def render_example_unit(unit: Any, *, output_field_names: list[str] | None = None) -> str:
    """A real unit stops the per-sample-vs-dataset conflation: ``summary.rows``
    is a per-row field, not the dataset's ``total_rows``."""
    if unit is None:
        return ""
    traj = getattr(unit, "trajectory", None) or {}
    lines = ["### Example sample (one real row — bind against THESE concrete shapes)"]

    final_output = _parse_json_maybe(traj.get("final_output") or "")
    if isinstance(final_output, dict):
        lines.append(
            f"output (source 'output') is a JSON object with top-level keys: "
            f"{', '.join(map(str, final_output.keys())) or '(empty object)'}"
        )
    elif isinstance(final_output, list):
        lines.append(f"output (source 'output') is a JSON array of {len(final_output)} item(s)")
    else:
        preview = str(final_output)[:200].replace("\n", " ")
        lines.append(f"output (source 'output') is plain text — first 200 chars: {preview!r}")

    expected = _parse_json_maybe(getattr(unit, "expected", None))
    if isinstance(expected, dict):
        lines.append(
            f"reference (source 'reference') is a JSON object with keys: "
            f"{', '.join(map(str, expected.keys())) or '(empty object)'}"
        )
        if expected:
            lines.append(
                "Observed reference leaf paths (bind source 'reference' + this exact jsonpath):"
            )
            lines.extend(
                f"- {path} ({kind})" for path, kind in _leaf_paths(expected)[:_REFERENCE_PATH_CAP]
            )
    elif expected in (None, "", []):
        lines.append("reference (source 'reference') is absent for this row")
    else:
        lines.append(f"reference (source 'reference') is {type(expected).__name__}")

    structured = getattr(unit, "structured", None) or {}
    tool_nodes = (structured.get("tool_graph") or {}).get("nodes") or []
    tool_names = [
        str(n.get("tool") or n.get("name") or "") for n in tool_nodes if isinstance(n, dict)
    ]
    tool_names = [t for t in tool_names if t]
    if tool_names:
        lines.append(f"tool_calls (source 'tool_calls') present: {', '.join(tool_names[:10])}")

    if output_field_names and isinstance(expected, dict) and expected:
        lines.append("")
        lines.extend(_surface_table_lines(expected, output_field_names))

    lines.append("")
    lines.append(
        "Per-row caveat: fields inside this output describe THIS single row "
        "(e.g. a per-row count), not dataset-wide totals — never bind a per-row "
        "output field to a dataset-level quantity."
    )
    lines.append("")
    lines.append(_SOURCE_TYPE_TABLE)
    return "\n".join(lines)


# chars; keeps a tool-heavy card from dominating the prompt.
_MAPPING_CONTEXT_BUDGET = 4000

# UNDECLARED is the novel-tool deviation signal.
UNCLUSTERED = "(unclustered)"
UNDECLARED = "(undeclared)"


def cluster_occupancy(tools_called: list[str], tool_spec: list[Any]) -> dict[str, list[str]]:
    """Joins via ``canonical_tool_name`` to the card's ``tool_spec``; the
    contract ``tool_set`` lacks ``cluster``."""
    spec_by_canon: dict[str, dict[str, Any]] = {}
    for tool in tool_spec or []:
        if isinstance(tool, dict) and str(tool.get("name") or "").strip():
            spec_by_canon.setdefault(canonical_tool_name(tool["name"]), tool)
    out: dict[str, list[str]] = {}
    seen: set[str] = set()
    for name in tools_called or []:
        if not str(name or "").strip():
            continue
        canon = canonical_tool_name(name)
        if canon in seen:
            continue
        seen.add(canon)
        spec = spec_by_canon.get(canon)
        if spec is None:
            cluster = UNDECLARED
        else:
            cluster = str(spec.get("cluster") or "").strip() or UNCLUSTERED
        out.setdefault(cluster, []).append(canon)
    return out


def capability_mapping_context(capability, behaviour_key: str = "") -> str:
    """From the cached capability card: zero extra queries."""
    card = (getattr(capability, "improvement_metadata", None) or {}).get("capability_card")
    if not isinstance(card, dict):
        return ""
    lines: list[str] = []
    paths = [p for p in card.get("trajectory_map") or [] if isinstance(p, dict) and p.get("id")]
    bound = (
        next((p for p in paths if str(p.get("id")) == behaviour_key), None)
        if behaviour_key
        else None
    )
    if bound is not None:
        terminal = bound.get("terminal") if isinstance(bound.get("terminal"), dict) else {}
        lines.append(
            f"Bound path: {bound.get('id')} — routing: {bound.get('routing') or 'unspecified'}; "
            f"declared terminal (evidence, not required): {terminal.get('kind') or 'unknown'}"
        )
    for surface in paths:
        if str(surface.get("claim") or "") != "decision_surface":
            continue
        lines.append(
            f"Decision surface: {surface.get('id')} — "
            f"{surface.get('routing') or 'a model chooses the route at runtime'}; "
            "its option space is the full declared tool set below"
        )
    by_cluster: dict[str, list[str]] = {}
    for tool in card.get("tool_spec") or []:
        if not isinstance(tool, dict) or not str(tool.get("name") or "").strip():
            continue
        cluster = str(tool.get("cluster") or "").strip() or UNCLUSTERED
        purpose = str(tool.get("purpose") or "").strip()
        side_effect = str(tool.get("side_effect") or "").strip()
        detail = "; ".join(
            part for part in (purpose, f"side_effect={side_effect}" if side_effect else "") if part
        )
        by_cluster.setdefault(cluster, []).append(
            f"{tool['name']}" + (f" — {detail}" if detail else "")
        )
    for cluster, tools in by_cluster.items():
        lines.append(f"Cluster '{cluster}':")
        lines.extend(f"  - {t}" for t in tools)
    text = "\n".join(lines)
    if len(text) > _MAPPING_CONTEXT_BUDGET:
        text = text[:_MAPPING_CONTEXT_BUDGET].rstrip() + " …(truncated)"
    return text


def render_grounding_pack(
    grounding: EvalGroundingContext,
    *,
    sample_rows: list[dict[str, Any]] | None = None,
) -> str:
    """Empty when nothing resolved — callers then fall back to raw sampling."""
    sections: list[str] = []
    prompt_block = _prompt_section(grounding.prompt_text, grounding.prompt_label)
    if prompt_block:
        sections.append(prompt_block)
    if grounding.dataset_card:
        sections.append(_dataset_section(grounding.dataset_card, grounding.data_version))
    if grounding.codebase_card:
        sections.append(_codebase_section(grounding.codebase_card, grounding.codebase_commit))
    if grounding.report:
        sections.append(_report_section(grounding.report))
    if sample_rows:
        lines = ["### Representative rows"]
        for i, row in enumerate(sample_rows, 1):
            lines.append(f"[{i}] Input:    {str(row.get('input', ''))[:600]}")
            lines.append(f"    Expected: {str(row.get('expected_output', ''))[:600]}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
