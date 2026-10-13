"""The Default eval set sync keeps for each capability: the expectations its code declares,
its toml expectations and deterministic checks, plus the seeded checks that apply to its spans."""

from __future__ import annotations

from django.utils import timezone

from overbae.innermind import checks
from overbae.models import Capability, EvalMatrix, EvalSet, Span

NAME = "Default"
EXPECTATION_LIMIT = 1_000

# Managed toml metrics a seeded check or the trajectory scorer already covers.
COVERED = {
    "json validity": "output_is_valid_json",
    "exact match": "exact_match",
    "task completion": None,
    "tool selection quality": "tool_selection",
    "trajectory accuracy": None,
}

MANAGED_EXPECTATIONS = {
    "correctness": "The output is factually correct relative to the reference answer.",
    "faithfulness": "Every claim in the output is grounded in the provided context.",
    "hallucination": "The output states nothing the input or context does not support.",
    "answer relevance": "The answer addresses the question asked.",
    "conciseness": "The answer is concise without omitting needed information.",
    "toxicity": "The output is free of harmful, offensive or disrespectful content.",
    "role adherence": "The assistant stays within its assigned role and persona.",
    "policy compliance": "The output follows the stated policy and guardrails.",
    "translation quality": "The translation is accurate, fluent and natural in the target language.",
}


def expectation_of(entry: dict) -> str:
    """One sentence the output must meet: what the entry measures, else the managed sentence."""
    text = str(entry.get("measures") or "").strip()
    if text:
        return text[:EXPECTATION_LIMIT]
    managed = str(entry.get("managed_name") or entry.get("name") or "").strip().lower()
    return MANAGED_EXPECTATIONS.get(managed, "")


def expectation_rows(capability: Capability) -> list[EvalMatrix]:
    """Upserts one llm row per declared expectation; the toml owns their text."""
    rows = []
    for entry in (capability.improvement_metadata or {}).get("eval_matrix") or []:
        if not isinstance(entry, dict) or entry.get("type") == "deterministic":
            continue
        managed = str(entry.get("managed_name") or "").strip().lower()
        if managed in COVERED:
            continue
        name = str(entry.get("name") or entry.get("managed_name") or "").strip()[:255]
        source = expectation_of(entry)
        if not name or not source:
            continue
        row, _ = EvalMatrix.objects.update_or_create(
            project_id=capability.project_id,
            capability=capability,
            name=name,
            defaults={"type": EvalMatrix.Type.LLM, "source": source},
        )
        rows.append(row)
    return rows


def declared_rows(capability: Capability) -> list[EvalMatrix]:
    """Upserts one llm row per ``expectations=`` sentence the synced checkout declares, scoped
    to its span, and deletes the rows of sentences the code no longer declares. The sentence
    is the row's name, so a live span carrying the same expectation is judged once."""
    meta = capability.improvement_metadata or {}
    declared: dict[str, str] = {}
    for entry in meta.get("expectations") or []:
        sentence = str(entry.get("expectation") or "").strip()[:EXPECTATION_LIMIT]
        if sentence:
            declared.setdefault(sentence, str(entry.get("span_name") or "")[:255])
    rows = []
    for sentence, span_name in declared.items():
        row, _ = EvalMatrix.objects.update_or_create(
            project_id=capability.project_id,
            capability=capability,
            name=sentence[:255],
            defaults={"type": EvalMatrix.Type.LLM, "source": sentence, "span_name": span_name},
        )
        rows.append(row)
    names = {row.name for row in rows}
    dropped = set((meta.get("eval_preload") or {}).get("declared") or []) - names
    EvalMatrix.objects.filter(
        project_id=capability.project_id,
        capability=capability,
        type=EvalMatrix.Type.LLM,
        name__in=dropped,
    ).delete()
    return rows


def deterministic_rows(capability: Capability) -> list[EvalMatrix]:
    """Upserts one deterministic row per toml entry that names a catalog ``check``; the toml
    owns the script's constants (``params``) and its span scope."""
    rows = []
    for entry in (capability.improvement_metadata or {}).get("eval_matrix") or []:
        if not isinstance(entry, dict) or entry.get("type") != "deterministic":
            continue
        check = str(entry.get("check") or "").strip()
        name = str(entry.get("name") or check).strip()[:255]
        row, _ = EvalMatrix.objects.update_or_create(
            project_id=capability.project_id,
            capability=capability,
            name=name,
            defaults={
                "type": EvalMatrix.Type.DETERMINISTIC,
                "source": checks.render(check, entry.get("params") or {}),
                "span_type": str(entry.get("span_type") or ""),
                "span_name": str(entry.get("span_name") or "")[:255],
            },
        )
        rows.append(row)
    return rows


def check_rows(capability: Capability) -> list[EvalMatrix]:
    span_types = set(
        Span.objects.filter(capability=capability)
        .values_list("span_type", flat=True)
        .distinct()[:20]
    )
    seeded = checks.seed(capability.project)
    names = {
        check
        for entry in (capability.improvement_metadata or {}).get("eval_matrix") or []
        if isinstance(entry, dict)
        and (check := COVERED.get(str(entry.get("managed_name") or "").strip().lower()))
    }
    requested = EvalMatrix.objects.filter(
        project_id=capability.project_id, capability=None, name__in=names, enabled=True
    )
    return [
        row
        for row in {row.pk: row for row in [*seeded, *requested]}.values()
        if not row.span_type or row.span_type in span_types
    ]


def upsert(capability: Capability) -> EvalSet:
    """Rebuilds the Default set's rows and activates it when the capability has no set."""
    started = timezone.now()
    declared = declared_rows(capability)
    expectations = [*expectation_rows(capability), *declared]
    seeded = [*deterministic_rows(capability), *check_rows(capability)]
    eval_set, _ = EvalSet.objects.get_or_create(
        project_id=capability.project_id, capability=capability, name=NAME
    )
    eval_set.rows.set([*expectations, *seeded])
    if capability.active_eval_set_id is None:
        capability.active_eval_set = eval_set
    meta = dict(capability.improvement_metadata or {})
    meta["eval_preload"] = {
        "status": "ready" if expectations or seeded else "empty",
        "started_at": started.isoformat(),
        "finished_at": timezone.now().isoformat(),
        "error": None,
        "counts": {"expectations": len(expectations), "checks": len(seeded)},
        "declared": sorted(row.name for row in declared),
    }
    capability.improvement_metadata = meta
    capability.save(update_fields=["active_eval_set", "improvement_metadata", "updated_at"])
    return eval_set
