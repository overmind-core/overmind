"""Where to place the SDK decorators, and whether a trace carries what live scoring reads.

Both are read-only: the plan comes from the capability card's anchors, and
verification grades spans in memory against the project's enabled matrix rows.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from overbae.api import overmind_attrs as oc_attrs
from overbae.innermind import evidence
from overbae.innermind.span_processor import span_processor_factory
from overbae.models import Capability, EvalMatrix, Span
from overbae.services.capabilities import identity
from overbae.services.codebase.anchors import module_dotted_path

DECORATORS = {
    "entry_point": '@overmind.entry_point(capability_id="{capability_id}")',
    "workflow": "@overmind.workflow()",
    "tool": "@overmind.tool()",
    "retrieval": "@overmind.retrieval()",
    "function": "@overmind.observe()",
}
SPAN_FIELDS = (
    "span_id",
    "trace_id",
    "parent_span_id",
    "span_type",
    "name",
    "start_time_ns",
    "end_time_ns",
    "attributes",
    "events",
    "resource_attrs",
    "status_code",
)
REQUIRED_FIELDS = ("span_id", "trace_id", "name")

GUIDANCE = {
    "identity": "Stamp overmind.capability.id: overmind.init(capability_id=...) or "
    "@overmind.entry_point(capability_id=...).",
    "root": "Decorate the capability's entry point so each run has one root span.",
    "input": "Capture the entry point's input: keep capture='auto'; when it lives on the "
    "instance, add self_attrs=['<attribute>'] (or pass format_input).",
    "output": "Return the answer from the entry point so its output is captured.",
    "tools": "Decorate each tool with @overmind.tool() so trajectory metrics see the calls.",
    "scored_rows": "Enable an eval matrix row that applies to these spans, or declare "
    "expectations on @overmind.observe(expectations=[...]).",
}


def target(qualname: str, file_ref: str) -> dict[str, str]:
    file = file_ref.split("#", 1)[0]
    module = module_dotted_path(file) if file else ""
    local = (
        qualname[len(module) + 1 :] if module and qualname.startswith(module + ".") else qualname
    )
    symbol = local.split(".")[0] if local else ""
    return {
        "file": file,
        "qualname": qualname,
        "module": module,
        "import_line": f"from {module} import {symbol}" if module and symbol else "",
    }


def placements(capability: Capability) -> list[dict[str, Any]]:
    card = (capability.improvement_metadata or {}).get("capability_card") or {}
    capability_id = str(capability.id)
    rows = []
    for anchor in card.get("anchors") or []:
        if not isinstance(anchor, dict) or not anchor.get("qualname"):
            continue
        kind = anchor.get("kind") if anchor.get("kind") in DECORATORS else "function"
        rows.append(
            {
                "capability": capability.name,
                "capability_id": capability_id,
                "kind": kind,
                "target": target(str(anchor["qualname"]), str(anchor.get("file") or "")),
                "required_decorator": DECORATORS[kind].format(capability_id=capability_id),
            }
        )
    return sorted(rows, key=lambda row: (row["kind"] != "entry_point", row["target"]["file"]))


def plan(project, capability: Capability | None = None) -> list[dict[str, Any]]:
    capabilities = (
        [capability]
        if capability is not None
        else list(Capability.objects.filter(project=project).current().order_by("name"))
    )
    return [row for cap in capabilities for row in placements(cap)]


def build_spans(project_id, payload: list[dict]) -> tuple[list[Span], list[dict]]:
    spans, errors = [], []
    for index, raw in enumerate(payload):
        missing = [key for key in REQUIRED_FIELDS if not raw.get(key)]
        if missing:
            errors.append({"index": index, "error": f"missing {', '.join(missing)}"})
            continue
        spans.append(Span(project_id=project_id, **{k: raw[k] for k in SPAN_FIELDS if k in raw}))
    return spans, errors


def resolve_identity(project_id, span: Span) -> Capability | None:
    for attrs in (span.attributes or {}, span.resource_attrs or {}):
        if found := attrs.get(oc_attrs.CAPABILITY_ID):
            return identity.lookup(project_id, str(found))
    return None


def grade(
    project_id, spans: list[Span], fallback: Capability | None = None
) -> list[dict[str, Any]]:
    """One result per capability: each check's state and how to fix what is missing."""
    for span in spans:
        if span.capability_id is None:
            span.capability = resolve_identity(project_id, span)
    by_id = {span.span_id: span for span in spans}
    for span in spans:
        ancestor, seen = span, set()
        while ancestor.capability_id is None and ancestor.parent_span_id in by_id:
            if ancestor.span_id in seen:
                break
            seen.add(ancestor.span_id)
            ancestor = by_id[ancestor.parent_span_id]
        if span.capability_id is None:
            span.capability = ancestor.capability if ancestor.capability_id else fallback
    matrix = list(EvalMatrix.objects.filter(project_id=project_id, enabled=True))
    groups: dict[Capability | None, list[Span]] = defaultdict(list)
    for span in spans:
        groups[span.capability].append(span)
    results = []
    for capability, members in groups.items():
        roots = [span for span in members if span.parent_span_id in (None, "")]
        root = roots[0] if roots else None
        tool_spans = [span for span in members if evidence.is_tool(span)]
        scored = {
            span.span_id: [
                row.name
                for row in span_processor_factory.get_processor(span).rows(span, matrix, by_id)
            ]
            for span in members
        }
        checks = {
            "identity": capability is not None,
            "root": len(roots) == 1,
            "input": bool(root and evidence.read_input(root)),
            "output": bool(root and evidence.read_output(root)),
            "tools": bool(tool_spans) or not _declares_tools(capability),
            "scored_rows": any(scored.values()),
        }
        results.append(
            {
                "capability": capability.name if capability else None,
                "capability_id": str(capability.id) if capability else None,
                "checks": {name: "ok" if ok else "missing" for name, ok in checks.items()},
                "punch_list": [
                    {"check": name, "instruction": GUIDANCE[name]}
                    for name, ok in checks.items()
                    if not ok
                ],
                "spans": [
                    {
                        "span_id": span.span_id,
                        "name": span.name,
                        "span_type": span.span_type,
                        "rows": scored[span.span_id][:20],
                    }
                    for span in members[:50]
                ],
            }
        )
    return results


def _declares_tools(capability: Capability | None) -> bool:
    card = (
        (capability.improvement_metadata or {}).get("capability_card") or {} if capability else {}
    )
    return bool(card.get("tool_spec"))


def verify_trace(project_id, trace_id: str, capability: Capability | None = None) -> list[dict]:
    spans = list(
        Span.objects.filter(project_id=project_id, trace_id=trace_id)
        .select_related("capability")
        .order_by("start_time_ns")
    )
    return grade(project_id, spans, capability)
