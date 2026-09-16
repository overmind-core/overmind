"""Population-wide health checks over the generative evaluator library.

Every fix in the reliability programme was verified on one agent's suite. These
are the same checks applied to every evaluator, so a defect class found once is
found everywhere without anyone going to look.

Generative only, deliberately. Trace scoring records verdicts on
``Span.feedback_score`` rather than in ``Score``, so measuring it against these
queries reports every one of its members as unscored. It is a separate system on
its own rebuild and owns its own invariants.
"""

from __future__ import annotations

import re
from collections import defaultdict
from typing import Any

from overbae.models import EvalSetMember, Evaluator, Score
from overbae.services.eval.sanitation import grades_stated_confidence

# A generative judge answers checklist items and nothing else — the score is
# computed in Python from its verdicts. But ``rubric_md`` reaches the judge
# verbatim on every call, so a rubric written for the older "return me a number"
# shape argues with the runtime: it names weights the aggregator does not use,
# promises floors the runtime does not apply, and asks for a score the prompt
# then forbids. Checked against the rubric rather than the compiled checklist,
# because that is the half a hand-patch leaves behind.
_RUBRIC_SCORING_MACHINERY = {
    "asks the judge for a score": re.compile(
        r"(output|return|emit|compute|produce|assign)\s+(a |an |the |exactly one )?"
        r"(single |final |overall |numeric )*score"
        r"|score between 0|on a 0\.0.{0,4}1\.0 scale",
        re.I,
    ),
    "states its own weights": re.compile(
        r"weight\s*0?\.\d|—\s*weight|weighted (checklist|sum)", re.I
    ),
    "promises a hard fail": re.compile(
        r"hard.?fail|overall score (MUST be|=) 0\.0|final score = 0\.0", re.I
    ),
    "describes its own arithmetic": re.compile(
        r"renormali|sum_over_checks|rounded to (three|3) decimals", re.I
    ),
}

# Below this many observed failures a co-failure ratio is noise.
_MIN_FAILING_ROWS = 5
# Above this share, one defect is costing most of the score — the items are
# almost certainly rephrasings of a single concern.
_CO_FAILURE_SUSPECT = 0.60
# At or below this many distinct verdict combinations the items never varied
# independently, so the co-failure ratio says nothing about item overlap.
_DEGENERATE_PATTERNS = 2


def _items(evaluator) -> list[dict]:
    return [i for i in (evaluator.checklist or []) if isinstance(i, dict)]


def _generative_members(project_id: str = ""):
    members = EvalSetMember.objects.filter(
        role=EvalSetMember.Role.GENERATIVE, evaluator__is_archived=False
    )
    return members.filter(evaluator__project_id=project_id) if project_id else members


def audit_evaluators(project_id: str = "") -> dict[str, list[dict[str, Any]]]:
    """Findings keyed by defect class, each entry naming the evaluator.

    The authoring checks below sweep the whole library, including rows no set
    uses yet: a malformed evaluator is malformed whoever runs it, and an unused
    template is what the next install starts from. Only the observation checks
    are generative-scoped, because only ``Score`` is.
    """
    alive = Evaluator.objects.filter(is_archived=False)
    if project_id:
        alive = alive.filter(project_id=project_id)

    # Only a generative judge is forbidden from scoring itself; trace scoring
    # asks the model for a number by design, so its rubrics may say so.
    scores_from_verdicts = set(
        _generative_members(project_id).values_list("evaluator_id", flat=True)
    )

    missing_checklist: list[dict[str, Any]] = []
    unbound: list[dict[str, Any]] = []
    confidence: list[dict[str, Any]] = []
    rubric_machinery: list[dict[str, Any]] = []
    for ev in alive:
        ref = {"evaluator": ev.name, "version": ev.version, "id": str(ev.id)}
        if ev.requires_checklist() and not ev.checklist:
            missing_checklist.append(ref)
        missing_vars = ev.unbound_checklist_variables()
        if missing_vars:
            unbound.append({**ref, "variables": missing_vars})
        offending = [str(i.get("id")) for i in _items(ev) if grades_stated_confidence(i)]
        if offending:
            confidence.append({**ref, "items": offending})
        if ev.id in scores_from_verdicts:
            found = [
                label
                for label, rx in _RUBRIC_SCORING_MACHINERY.items()
                if rx.search(ev.rubric_md or "")
            ]
            if found:
                rubric_machinery.append({**ref, "contradictions": found})

    # An evaluator a set enables but that stayed silent through a run it was in
    # is an unknown, not a pass. An agent nobody has ever evaluated is a
    # different condition entirely and says nothing about its evaluators, so the
    # two are reported apart rather than summed into one alarming number.
    scored = set(Score.objects.values_list("name", flat=True).distinct())
    # An evaluator authored AFTER its agent's last run has not been silent, it
    # has not been asked yet. Without the timestamp an agent collects a fresh
    # finding every time its suite grows, which is how a check earns a
    # reputation for crying wolf.
    last_scored_at: dict[Any, Any] = {}
    for capability_id, scored_at in Score.objects.filter(
        run__dataset__capability__isnull=False
    ).values_list("run__dataset__capability_id", "created_at"):
        if capability_id not in last_scored_at or scored_at > last_scored_at[capability_id]:
            last_scored_at[capability_id] = scored_at

    never_scored: list[dict[str, Any]] = []
    never_evaluated: list[dict[str, Any]] = []
    seen: set[tuple[str, Any]] = set()
    for member in (
        _generative_members(project_id).filter(enabled=True).select_related("evaluator", "eval_set")
    ):
        name = member.evaluator.name
        capability_id = member.eval_set.capability_id
        if name in scored or (name, capability_id) in seen:
            continue
        seen.add((name, capability_id))
        entry = {"evaluator": name, "capability_id": str(capability_id) if capability_id else None}
        # The MEMBER's age, not the evaluator's: a long-standing evaluator that
        # only just joined this set under this role was not silent through the
        # runs that predate its membership.
        last = last_scored_at.get(capability_id)
        was_asked = last is not None and member.created_at <= last
        (never_scored if was_asked else never_evaluated).append(entry)

    return {
        "missing_checklist": missing_checklist,
        "unbound_variables": unbound,
        "confidence_comparisons": confidence,
        "rubric_scoring_machinery": rubric_machinery,
        "never_scored": sorted(never_scored, key=lambda row: row["evaluator"]),
        "never_evaluated": sorted(never_evaluated, key=lambda row: row["evaluator"]),
        "co_failure": _co_failure(project_id),
    }


def _co_failure(project_id: str = "") -> list[dict[str, Any]]:
    """Share of an evaluator's items that fail on a row where anything failed.

    Near 1.0 means one defect is costing the whole score. Two very different
    causes produce that, so the distinct verdict patterns are counted alongside:

    * many patterns, high ratio — the items are rephrasings of one concern, and
      a real defect trips all of them (`Correctness` before it was restructured);
    * one or two patterns — the items never vary independently at all, which is
      what a fabricated score looks like. `seed_demo` stamps every item of
      `Resolution Policy Compliance` from a single boolean, so it reads 1.00 here
      while being no evidence about the evaluator.
    """
    tally: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"rows": 0, "failed": 0, "items": 0, "patterns": set()}
    )
    # Generative by construction: trace scoring writes no ``Score`` rows.
    rows = Score.objects.filter(sample__isnull=False).exclude(name__endswith="__prediction")
    if project_id:
        rows = rows.filter(project_id=project_id)
    for score in rows.only("name", "sub_scores"):
        items = [
            i
            for i in (score.sub_scores or [])
            if isinstance(i, dict) and i.get("id") and "verdict" in i
        ]
        if not items:
            continue
        entry = tally[score.name]
        entry["patterns"].add(tuple(sorted((str(i["id"]), bool(i["verdict"])) for i in items)))
        failed = sum(1 for i in items if i["verdict"] is False)
        if not failed:
            continue
        entry["rows"] += 1
        entry["failed"] += failed
        entry["items"] += len(items)

    findings = []
    for name, t in tally.items():
        if t["rows"] < _MIN_FAILING_ROWS:
            continue
        ratio = t["failed"] / t["items"]
        patterns = len(t["patterns"])
        findings.append(
            {
                "evaluator": name,
                "rows": t["rows"],
                "ratio": ratio,
                "patterns": patterns,
                # A judge that emits only all-pass or all-fail is telling us
                # nothing about item overlap, so it is reported separately.
                "suspect": ratio >= _CO_FAILURE_SUSPECT and patterns > _DEGENERATE_PATTERNS,
                "degenerate": patterns <= _DEGENERATE_PATTERNS,
            }
        )
    return sorted(findings, key=lambda row: -row["ratio"])
