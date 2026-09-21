"""EvalSet lifecycle. Authoring is live: a set runs as soon as it has members."""

from __future__ import annotations

import logging

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from overbae.models import Capability, EvalSet, EvalSetMember, Evaluator, Project, RunEvaluator
from overbae.services.eval import snapshots
from overbae.services.eval.roles import roles_for_evaluator
from overbae.services.eval.specs import AUTHORED_GENERATORS, TIER0_GENERATOR

logger = logging.getLogger(__name__)


@transaction.atomic
def create_with_evaluators(
    *, project, name, capability=None, created_by=None, evaluators=(), description="", prompts=()
) -> EvalSet:
    if capability is not None and (
        capability.project_id != project.id or capability.status != Capability.Status.CURRENT
    ):
        raise ValidationError({"capability": "Select a current capability in this project."})
    Project.objects.select_for_update().get(pk=project.pk)
    if EvalSet.objects.filter(project=project, capability=capability, name=name).exists():
        raise ValidationError({"name": "An eval set with this name already exists."})

    evaluator_ids = {ev.id for ev in evaluators}
    available = Evaluator.objects.filter(
        Q(project=project) | Q(project__isnull=True, is_managed=True)
    ).visible_catalog()
    if evaluator_ids - set(available.values_list("id", flat=True)):
        raise ValidationError({"evaluator_ids": "Select evaluators from this project's library."})
    members = []
    taken = set()
    for ev in dict.fromkeys(evaluators):
        roles = roles_for_evaluator(ev)
        if not roles:
            raise ValidationError({"evaluator_ids": f"'{ev.name}' has no runnable roles."})
        for role in roles:
            key = (ev.name, role)
            if key in taken:
                # Run summaries key scores by name, so two rows would double-weight a metric.
                raise ValidationError(
                    {"evaluator_ids": f"Select only one evaluator named '{ev.name}' per role."}
                )
            taken.add(key)
            members.append(EvalSetMember(evaluator=ev, role=role, order=len(members)))

    eval_set = EvalSet.objects.create(
        project=project,
        capability=capability,
        name=name,
        description=description,
        created_by=created_by,
    )
    eval_set.prompts.set(prompts)
    for member in members:
        member.eval_set = eval_set
    EvalSetMember.objects.bulk_create(members)
    return (
        EvalSet.objects.select_related("capability")
        .prefetch_related("members__evaluator__capability", "prompts")
        .get(pk=eval_set.pk)
    )


def eval_dataset_ready_for_sync(dataset) -> bool:
    """An eval dataset with rows, linked to a capability — enough to mint label-accuracy."""
    from overbae.models import Dataset

    if dataset is None or dataset.capability_id is None:
        return False
    if dataset.intent != Dataset.Intent.EVAL:
        return False
    cell = dataset.active_cell
    if cell is None or cell.rows <= 0:
        return False
    ok, _reason = cell.fits("eval")
    return ok


def maybe_enqueue_card_evaluator_sync(dataset) -> None:
    """Backfill Tier-0 evaluators when eval data lands on a capability."""
    from django.db import transaction

    if not eval_dataset_ready_for_sync(dataset):
        return
    from overbae.tasks.eval import sync_card_evaluators_task

    capability_id = str(dataset.capability_id)
    transaction.on_commit(lambda: sync_card_evaluators_task.delay(capability_id=capability_id))


def active_members(eval_set: EvalSet, role: str):
    """Enabled members in role order, excluding archived evaluators."""
    return (
        eval_set.members.filter(role=role, enabled=True, evaluator__is_archived=False)
        .select_related("evaluator")
        .order_by("order", "created_at")
    )


def resolve_members(eval_set: EvalSet, role: str = EvalSetMember.Role.GENERATIVE) -> list:
    members = active_members(eval_set, role)
    return [member.evaluator for member in members if member.evaluator_id]


# The eval-matrix producer is gone, but deployed DBs still hold its sentinel
# rows (config/weight carriers, not runnable graders) — these filters must
# outlive it so the sentinels never surface as graders.
NON_GRADING_ROLES = frozenset({"spec", "structure", "tool_usage"})
NON_GRADING_NAMES = frozenset({"__agent_spec__", "__structure__", "__tool_usage__"})


def runnable_capability_evaluators(capability: Capability) -> list[Evaluator]:
    """An active :class:`EvalSet` with generative members resolves the grader
    set, so the optimizer, backtest and run wizard share one source of truth.
    Falls back to a per-capability FK scan otherwise, always excluding the
    sentinel rows."""
    if capability.active_eval_set_id is not None:
        members = resolve_members(capability.active_eval_set)
        if members:
            return members
    return [
        ev
        for ev in Evaluator.objects.filter(
            capability=capability, is_managed=False, is_archived=False
        )
        if (ev.config or {}).get("capability_spec_role") not in NON_GRADING_ROLES
        and ev.name not in NON_GRADING_NAMES
    ]


def expand_to_run_evaluators(
    run, eval_set: EvalSet, *, role: str = EvalSetMember.Role.GENERATIVE
) -> list[RunEvaluator]:
    """Must mirror the ``evaluator_ids`` snapshotting in ``EvalRunSerializer.create()``."""
    members = active_members(eval_set, role)
    # prompt=None grades every variant.
    set_prompt_ids = list(eval_set.prompts.values_list("id", flat=True))

    created: list[RunEvaluator] = []
    order = 0
    for member in members:
        if not member.evaluator_id:
            continue
        if member.prompt_id:
            prompt_ids: list = [member.prompt_id]
        elif set_prompt_ids:
            prompt_ids = set_prompt_ids
        else:
            prompt_ids = [None]
        snapshot = snapshots.build_snapshot(member.evaluator)
        for prompt_id in prompt_ids:
            created.append(
                RunEvaluator.objects.create(
                    run=run,
                    evaluator=member.evaluator,
                    snapshot=snapshot,
                    prompt_id=prompt_id,
                    order=order,
                )
            )
            order += 1
    return created


def activate(capability: Capability, eval_set: EvalSet) -> EvalSet:
    capability.active_eval_set = eval_set
    capability.save(update_fields=["active_eval_set"])
    return eval_set


def next_member_order(eval_set: EvalSet) -> int:
    from django.db.models import Max

    current = eval_set.members.aggregate(m=Max("order")).get("m")
    return (current + 1) if current is not None else 0


def add_evaluator_as_member(
    eval_set: EvalSet, evaluator: Evaluator, role: str
) -> tuple[EvalSetMember, bool]:
    """Idempotent ``(set, evaluator, role)`` attach — shared by the eval-set
    ``members`` action and save-time attach from evaluator authoring."""
    return EvalSetMember.objects.get_or_create(
        eval_set=eval_set,
        evaluator=evaluator,
        role=role,
        defaults={"prompt": None, "order": next_member_order(eval_set)},
    )


def ensure_default_eval_set(capability: Capability, *, created_by=None) -> EvalSet:
    eval_set, _ = EvalSet.objects.get_or_create(
        capability=capability,
        name="Default",
        defaults={"project": capability.project, "created_by": created_by},
    )
    if capability.active_eval_set_id != eval_set.id:
        activate(capability, eval_set)
    return eval_set


def _merge_specs_into_set(
    capability: Capability, eval_set: EvalSet, specs: list, *, created_by=None
) -> dict:
    """Additive merge. Evaluator identity is ``(name, scope)``: a spec whose
    identity already has a live capability evaluator REUSES that row rather than
    minting a version. A grading-content change bumps ``version`` in place so
    persisted scores record which grader version produced them. Membership
    identity is ``(name, scope, role)``.
    """
    # Local imports keep this module out of the heavy generation import graph
    # and its cycle through the API layer.
    from django.db.models import Max

    from overbae.services.eval.authored import persist_specs
    from overbae.services.eval.roles import roles_for_spec
    from overbae.services.eval.specs import AUTHORED_GENERATORS

    # A stale membership would occupy the (name, scope, role) slot and keep
    # serving the archived version; hand-pinned members are never touched.
    eval_set.members.filter(
        evaluator__is_archived=True,
        evaluator__is_managed=False,
        evaluator__config__provenance__generator__in=tuple(AUTHORED_GENERATORS),
    ).delete()

    eval_by_key: dict[tuple[str, str], Evaluator] = {}
    for ev in Evaluator.objects.filter(capability=capability, is_archived=False).order_by(
        "version"
    ):
        eval_by_key[(ev.name, ev.scope)] = ev  # later (higher) version wins

    member_role_keys: set[tuple[str, str, str]] = set()
    for m in eval_set.members.select_related("evaluator"):
        # An archived evaluator is not in the live suite; its leftover member
        # must not hold the name against a re-authored replacement.
        if m.evaluator_id and not m.evaluator.is_archived:
            member_role_keys.add((m.evaluator.name, m.evaluator.scope, m.role))

    new_specs = []
    seen: set[tuple[str, str]] = set()
    refreshed = 0
    for spec in specs:
        key = (spec.name, spec.scope)
        if key in eval_by_key:
            if _refresh_reused_spec(eval_by_key[key], spec):
                refreshed += 1
            continue
        if key in seen:
            continue
        seen.add(key)
        new_specs.append(spec)

    created_count = 0
    if new_specs:
        for spec, evaluator in zip(
            new_specs,
            persist_specs(
                new_specs, project=capability.project, capability=capability, created_by=created_by
            ),
            strict=True,
        ):
            eval_by_key[(spec.name, spec.scope)] = evaluator
            created_count += 1

    current_max = eval_set.members.aggregate(m=Max("order")).get("m")
    order = (current_max + 1) if current_max is not None else 0
    added = {EvalSetMember.Role.GENERATIVE: 0, EvalSetMember.Role.TRACE_SCORING: 0}
    for spec in specs:
        evaluator = eval_by_key[(spec.name, spec.scope)]
        for role in roles_for_spec(spec):
            if (spec.name, spec.scope, role) in member_role_keys:
                continue
            _, created = EvalSetMember.objects.get_or_create(
                eval_set=eval_set,
                evaluator=evaluator,
                role=role,
                defaults={"prompt": None, "order": order},
            )
            if created:
                member_role_keys.add((spec.name, spec.scope, role))
                added[role] += 1
                order += 1
    return {
        "created": created_count,
        "updated": refreshed,
        "generative": added[EvalSetMember.Role.GENERATIVE],
        "trace_scoring": added[EvalSetMember.Role.TRACE_SCORING],
    }


# A refresh touching any of these bumps ``version`` in place.
_GRADING_FIELDS = ("checklist", "rubric_md", "variable_mapping", "pass_threshold", "config")


def _refresh_reused_spec(row: Evaluator, spec) -> bool:
    """In place, no duplicate row; a grading-content change bumps ``version``
    so every persisted score can record which grader version produced it.
    True when the row was written. A kind mismatch is left alone — an LLM
    judge must not be rewritten into a compiled check of the same name."""
    if row.kind != spec.kind:
        return False
    kwargs = spec.to_evaluator_kwargs()
    update_fields: list[str] = []
    for field in ("checklist", "rubric_md", "description", "display_name", "variable_mapping"):
        new = kwargs[field]
        if getattr(row, field) != new:
            setattr(row, field, new)
            update_fields.append(field)
    if row.pass_threshold != kwargs["pass_threshold"]:
        row.pass_threshold = kwargs["pass_threshold"]
        update_fields.append("pass_threshold")
    # Surface drives role routing; membership backfill reads the persisted row.
    if row.surface != kwargs["surface"]:
        row.surface = kwargs["surface"]
        update_fields.append("surface")
    # Keys other subsystems stamped onto the row survive.
    new_config = kwargs["config"]
    merged = {**(row.config or {}), **new_config}
    if "behaviour" not in new_config:
        merged.pop("behaviour", None)
    if merged != (row.config or {}):
        row.config = merged
        update_fields.append("config")
    if not update_fields:
        return False
    if any(f in update_fields for f in _GRADING_FIELDS):
        # The uniqueness constraint excludes scope, so a blind +1 can collide
        # with a same-named row of another scope or an archived twin.
        taken = set(
            Evaluator.objects.filter(project=row.project, capability=row.capability, name=row.name)
            .exclude(pk=row.pk)
            .values_list("version", flat=True)
        )
        version = (row.version or 1) + 1
        while version in taken:
            version += 1
        row.version = version
        update_fields.append("version")
    row.save(update_fields=update_fields)
    return True


# Card-compiler leftovers that must never sit on a live suite: these structural
# checks used to hard-gate the rest. Recompiles drop them here; live scoring
# (trace_scoring) also refuses to run them.
STRUCTURAL_TRACE_GATES = frozenset(
    {
        "output-schema-field-conformance",
        "tool-vocabulary-selection",
        "output-contract-required-keys",
        "output-parses-as-json",
    }
)


def _drop_stale_generated_trace_members(eval_set: EvalSet, specs: list) -> int:
    """Hand-authored and other-generator members stay."""
    keep_names = {s.name for s in specs}
    suite_generators = {s.provenance.generator for s in specs}
    dropped = 0
    members = eval_set.members.filter(role=EvalSetMember.Role.TRACE_SCORING).select_related(
        "evaluator"
    )
    for member in members:
        if member.evaluator.name in STRUCTURAL_TRACE_GATES:
            member.delete()
            dropped += 1
            continue
        if member.evaluator.name in keep_names:
            continue
        config = member.evaluator.config or {}
        if not config.get("behaviour"):
            continue
        gen = (config.get("provenance") or {}).get("generator")
        if gen not in suite_generators:
            continue
        member.delete()
        dropped += 1
    return dropped


def _drop_superseded_incumbent_members(eval_set: EvalSet, allocation: dict | None) -> int:
    """A stale incumbent loses its membership only once a current-contract judge
    claims its cell: coverage beats purity. Staleness is re-checked on the live
    row because a merge may have refreshed it in place."""
    from overbae.services.eval.specs import AUTHORED_GENERATORS, AUTHORING_CONTRACT

    if not isinstance(allocation, dict):
        return 0
    stale = allocation.get("stale_incumbents") or []
    if not stale:
        return 0
    claimant_by_cell: dict[tuple[str, str], str] = {}
    for cell in allocation.get("cells") or []:
        for suite, claimant in (cell.get("claimed") or {}).items():
            claimant_by_cell[(suite, str(cell.get("key")))] = str(claimant or "")
    dropped = 0
    for entry in stale:
        suite = str(entry.get("suite") or "")
        evaluator_id = str(entry.get("evaluator_id") or "")
        claimant = claimant_by_cell.get((suite, str(entry.get("cell") or "")), "")
        if not claimant or not evaluator_id:
            continue
        row = Evaluator.objects.filter(pk=evaluator_id).first()
        if row is None:
            continue
        provenance = (row.config or {}).get("provenance") or {}
        if provenance.get("generator") not in AUTHORED_GENERATORS:
            continue
        try:
            if int(provenance.get("authoring_contract") or 0) == AUTHORING_CONTRACT:
                continue
        except (TypeError, ValueError):
            pass
        dropped += eval_set.members.filter(role=suite, evaluator_id=evaluator_id).delete()[0]
    if dropped:
        logger.info(
            "[eval-preload] set %s retired %d stale-contract incumbent membership(s)",
            eval_set.id,
            dropped,
        )
    return dropped


def _drop_behaviour_generative_members(eval_set: EvalSet) -> int:
    """Machine-authored behaviour judges are trace-only: their rubric assumes
    the live conversation ledger, which generate-mode replay cannot reproduce.
    Hand-authored task-scoped judges may legitimately target generative replay
    and are left alone — same provenance check as
    ``_drop_stale_generated_trace_members``, so a resync never silently
    deletes a user's own generative membership."""
    dropped = 0
    members = eval_set.members.filter(
        role=EvalSetMember.Role.GENERATIVE, evaluator__config__behaviour__isnull=False
    ).select_related("evaluator")
    for member in members:
        generator = ((member.evaluator.config or {}).get("provenance") or {}).get("generator")
        if generator not in AUTHORED_GENERATORS:
            continue
        member.delete()
        dropped += 1
    return dropped


_COMPILER_CONTRACT_GATES = frozenset(
    {
        "output-field-accuracy",
        "output-contract-required-keys",
        "confidence-calibration",
        "output-parses-as-json",
        "summary-rows-authoritative",
    }
)


def _reconcile_compiler_memberships(eval_set: EvalSet, specs: list) -> int:
    """Drop card-compiler members whose role or identity this compile no longer warrants."""
    from overbae.services.eval.roles import roles_for_evaluator, roles_for_spec

    keep_roles = {spec.name: set(roles_for_spec(spec)) for spec in specs}
    dropped = 0
    members = eval_set.members.select_related("evaluator")
    for member in members:
        ev = member.evaluator
        generator = ((ev.config or {}).get("provenance") or {}).get("generator")
        if generator != TIER0_GENERATOR:
            continue
        if ev.name in keep_roles:
            if member.role not in keep_roles[ev.name]:
                member.delete()
                dropped += 1
            continue
        if ev.name in _COMPILER_CONTRACT_GATES:
            member.delete()
            dropped += 1
            continue
        if member.role not in roles_for_evaluator(ev):
            member.delete()
            dropped += 1
    return dropped


def _bind_judge_checklist_fields(capability: Capability, card: dict | None) -> int:
    """Backfill schema-derived ``field`` refs so a failed checklist item joins
    its ``schema_field`` graph node. Updating in place mints no version:
    ``field`` never reaches the judge prompt, it only stamps verdict sub_scores.
    Items a deterministic check already owns are dropped when anything remains.
    """
    from overbae.services.eval import card_compiler

    updated = 0
    judges = Evaluator.objects.filter(capability=capability, kind="llm_judge", is_archived=False)
    for evaluator in judges:
        checklist = evaluator.checklist or []
        bound, dropped = card_compiler.prepare_judge_checklist(checklist, card)
        if dropped and not bound:
            # Emptying a live judge recreates invented-checklist scoring. Bind only; leave the items.
            bound = card_compiler.bind_checklist_fields(
                checklist,
                card_compiler.card_output_field_names(card),
                tool_names=card_compiler.card_tool_names(card),
            )
            logger.warning(
                "card-sync: judge %s is entirely exactly-checkable fields; not emptying it",
                evaluator.name,
            )
        if bound != checklist:
            evaluator.checklist = bound
            evaluator.save(update_fields=["checklist"])
            updated += 1
    return updated


def _heal_generative_surface_bindings(eval_set: EvalSet, grounding) -> tuple[int, int]:
    """Drop generate-unobservable items, then fill generate-observable remainder.
    A judge that would empty is left intact and its generative membership is
    disabled — emptying recreates invented-checklist scoring. Heal never flips ``enabled`` back on."""
    from overbae.services.eval.card_compiler import generate_observes_tool_calls
    from overbae.services.eval.profiler import closed_form_reference_for
    from overbae.services.eval.sanitation import sanitize_authored_text
    from overbae.services.eval.surface_binding import (
        card_claims_for_source,
        enforce_surface_bindings,
        ensure_reference_variable_mapping,
        fill_generate_observable_remainder,
        rebind_query_items_to_gold,
    )

    observes = generate_observes_tool_calls(grounding)
    card = grounding.codebase_card
    closed_form = closed_form_reference_for(
        capability=getattr(eval_set, "capability", None),
        dataset=getattr(grounding, "dataset", None),
    )
    healed = 0
    disabled = 0
    seen: set[object] = set()
    members = eval_set.members.filter(role=EvalSetMember.Role.GENERATIVE).select_related(
        "evaluator"
    )
    for member in members:
        evaluator = member.evaluator
        if (
            not evaluator
            or evaluator.is_archived
            or evaluator.pk in seen
            or evaluator.kind != Evaluator.Kind.LLM_JUDGE
        ):
            continue
        roles = list(evaluator.applicable_roles or [])
        if EvalSetMember.Role.TRACE_SCORING in roles:
            continue
        if eval_set.members.filter(
            evaluator=evaluator, role=EvalSetMember.Role.TRACE_SCORING
        ).exists():
            continue
        seen.add(evaluator.pk)
        original = evaluator.checklist or []
        original_mapping = evaluator.variable_mapping or []
        source = str(((evaluator.config or {}).get("provenance") or {}).get("source") or "")
        sourced = card_claims_for_source(source, card)
        kept, mapping, notes, drop = enforce_surface_bindings(
            checklist=original,
            variable_mapping=original_mapping,
            rubric_md=evaluator.rubric_md or "",
            card=card,
            grades_live_surface=False,
            generate_observes_tools=observes,
            sourced_claims=sourced,
        )
        if closed_form:
            kept, rebind_notes = rebind_query_items_to_gold(kept)
            mapping = ensure_reference_variable_mapping(mapping)
            notes = list(notes) + rebind_notes
        kept, fill_notes = fill_generate_observable_remainder(
            kept,
            card,
            source,
            generate_observes_tools=observes,
            closed_form_reference=closed_form,
        )
        if fill_notes:
            kept, mapping, extra, drop = enforce_surface_bindings(
                checklist=kept,
                variable_mapping=mapping,
                rubric_md=evaluator.rubric_md or "",
                card=card,
                grades_live_surface=False,
                generate_observes_tools=observes,
                sourced_claims=sourced,
            )
            notes = list(notes) + fill_notes + extra
        generative_members = eval_set.members.filter(
            evaluator=evaluator, role=EvalSetMember.Role.GENERATIVE
        )
        if original and (drop or not kept):
            n = generative_members.filter(enabled=True).update(enabled=False)
            disabled += n
            if n:
                logger.warning(
                    "card-sync: disabled generative member(s) for %s — every item is "
                    "generate-unobservable",
                    evaluator.name,
                )
            continue
        if not kept:
            continue
        repaired = []
        for item in kept:
            if not isinstance(item, dict):
                repaired.append(item)
                continue
            q, _leaks = sanitize_authored_text(str(item.get("q") or ""))
            repaired.append(item if q == (item.get("q") or "") else {**item, "q": q})
        kept = repaired
        content_changed = kept != original or mapping != original_mapping or bool(fill_notes)
        if closed_form and not evaluator.requires_reference:
            evaluator.requires_reference = True
            content_changed = True
        if content_changed:
            rebuilt, _leaks = sanitize_authored_text(
                "\n".join(f"{i}. {entry.get('q')}" for i, entry in enumerate(kept, start=1))
            )
            evaluator.checklist = kept
            evaluator.variable_mapping = mapping
            evaluator.rubric_md = rebuilt
            config = dict(evaluator.config or {})
            if notes:
                config["_surface_repairs"] = notes
            evaluator.config = config
            taken = set(
                Evaluator.objects.filter(
                    project=evaluator.project, capability=evaluator.capability, name=evaluator.name
                )
                .exclude(pk=evaluator.pk)
                .values_list("version", flat=True)
            )
            version = (evaluator.version or 1) + 1
            while version in taken:
                version += 1
            evaluator.version = version
            update_fields = ["checklist", "variable_mapping", "rubric_md", "config", "version"]
            if closed_form:
                update_fields.append("requires_reference")
            evaluator.save(update_fields=update_fields)
            healed += 1
    return healed, disabled


def _enforce_behaviour_coverage(capability: Capability, eval_set: EvalSet, grounding) -> dict:
    """Each behaviour needs one outcome judge plus its step judges as live
    trace-scoring members."""
    from django.utils import timezone

    from overbae.services.eval import card_compiler

    expected = card_compiler.compile_behaviour_suites(grounding)
    if not expected:
        return {"complete": True, "behaviours": {}}
    live_trace_names = {
        m.evaluator.name
        for m in eval_set.members.filter(role=EvalSetMember.Role.TRACE_SCORING).select_related(
            "evaluator"
        )
        if m.evaluator_id and not m.evaluator.is_archived
    }
    behaviours: dict[str, dict] = {}
    for spec in expected:
        binding = (spec.config or {}).get("behaviour") or {}
        key = str(binding.get("behaviour_key") or "")
        entry = behaviours.setdefault(
            key, {"outcome": False, "steps_expected": 0, "steps_present": 0, "missing": []}
        )
        present = spec.name in live_trace_names
        if binding.get("role") == "outcome":
            entry["outcome"] = present
        else:
            entry["steps_expected"] += 1
            entry["steps_present"] += present
        if not present:
            entry["missing"].append(spec.name)
    complete = all(
        e["outcome"] and e["steps_present"] == e["steps_expected"] for e in behaviours.values()
    )
    coverage = {
        "complete": complete,
        "behaviours": behaviours,
        "checked_at": timezone.now().isoformat(),
    }
    if not complete:
        gaps = {k: e["missing"] for k, e in behaviours.items() if e["missing"]}
        logger.error(
            "[eval-coverage] capability %s behaviour coverage INCOMPLETE: %s", capability.id, gaps
        )
    meta = dict(capability.improvement_metadata or {})
    meta["eval_coverage"] = coverage
    capability.improvement_metadata = meta
    capability.save(update_fields=["improvement_metadata"])
    return coverage


def _persist_authoring_status(
    capability: Capability,
    *,
    drops: list[dict],
    suites_timed_out: list[str],
    synthetic_failures: list[dict],
    allocation: dict | None = None,
) -> None:
    from django.utils import timezone

    degraded_reasons = [f"tier1 {s} suite timed out" for s in suites_timed_out]
    degraded_reasons += [
        f"tier1 {d.get('suite') or '?'} suite truncated: {d.get('reason')}"
        for d in drops
        if d.get("stage") == "truncated"
    ]
    status = {
        "drops": drops,
        "drop_count": len(drops),
        "synthetic_failures": synthetic_failures,
        "degraded": bool(degraded_reasons),
        "degraded_reasons": degraded_reasons,
        "allocation": allocation,
        "updated_at": timezone.now().isoformat(),
    }
    if drops or synthetic_failures:
        logger.warning(
            "[eval-authoring] capability %s: %d authoring drop(s), %d synthetic-row failure(s): %s",
            capability.id,
            len(drops),
            len(synthetic_failures),
            [d.get("name") for d in drops + synthetic_failures],
        )
    meta = dict(capability.improvement_metadata or {})
    meta["eval_authoring"] = status
    capability.improvement_metadata = meta
    capability.save(update_fields=["improvement_metadata"])


def sync_card_evaluators(capability: Capability, *, created_by=None) -> dict:
    """No LLM calls; runs on every card write. Specs are NOT provenance-deduped
    so an existing evaluator still gets a missing membership backfilled."""
    from overbae.services.eval import card_compiler
    from overbae.services.eval.grounding import (
        attach_example_dataset,
        resolve_grounding_for_capability,
    )

    grounding = attach_example_dataset(resolve_grounding_for_capability(capability))
    specs = card_compiler.compile_managed_card_evaluators(grounding)
    if not specs:
        judges_bound = _bind_judge_checklist_fields(capability, grounding.codebase_card)
        logger.info(
            "[card-sync] capability %s has no card-derived evaluators to sync", capability.id
        )
        return {
            "eval_set_id": None,
            "synced": 0,
            "created": 0,
            "updated": 0,
            "added": 0,
            "judges_bound": judges_bound,
        }

    eval_set = ensure_default_eval_set(capability, created_by=created_by)
    from overbae.services.eval.binding_check import restore_sweep_quarantined

    restore_sweep_quarantined(capability=capability)
    result = _merge_specs_into_set(capability, eval_set, specs, created_by=created_by)
    dropped = _drop_stale_generated_trace_members(eval_set, specs)
    dropped_generative = _drop_behaviour_generative_members(eval_set)
    _reconcile_compiler_memberships(eval_set, specs)
    judges_bound = _bind_judge_checklist_fields(capability, grounding.codebase_card)
    surface_healed, surface_disabled = _heal_generative_surface_bindings(eval_set, grounding)
    coverage = _enforce_behaviour_coverage(capability, eval_set, grounding)
    total_added = result["generative"] + result["trace_scoring"]
    logger.info(
        "[card-sync] capability %s: %d spec(s), %d new evaluator(s), %d new member(s), "
        "%d judge checklist(s) field-bound, %d generative surface heal(s), "
        "%d generative member(s) disabled as unobservable, %d stale trace member(s) dropped",
        capability.id,
        len(specs),
        result["created"],
        total_added,
        judges_bound,
        surface_healed,
        surface_disabled,
        dropped,
    )
    return {
        "eval_set_id": str(eval_set.id),
        "synced": len(specs),
        "created": result["created"],
        "updated": result["updated"],
        "added": total_added,
        "dropped": dropped,
        "dropped_generative": dropped_generative,
        "judges_bound": judges_bound,
        "surface_healed": surface_healed,
        "surface_disabled": surface_disabled,
        "coverage_complete": coverage.get("complete"),
        **{k: result[k] for k in ("generative", "trace_scoring")},
    }


def _scan_matrix_hint(capability: Capability) -> list:
    """Toml ``eval_matrix``, as a hint for tier-1 authoring. Never materialized
    as evaluator rows.
    """
    matrix = (capability.improvement_metadata or {}).get("eval_matrix")
    return matrix if isinstance(matrix, list) else []


def _already_authored(capability: Capability) -> bool:
    """Whether tier 1 has ever produced judges for this capability.

    Deterministic specs dedup on ``(name, scope)``. Tier 1 is an LLM call, so
    identical grounding yields overlapping judges under new names and a re-scan
    stacks another layer. Tier 1 therefore runs once; re-authoring is deliberate.
    """
    from overbae.services.eval.specs import TIER1_GENERATOR

    return Evaluator.objects.filter(
        capability=capability, is_archived=False, config__provenance__generator=TIER1_GENERATOR
    ).exists()


def _retire_floor_judge(capability: Capability, *, covered: set[str]) -> int:
    """Disable the floor's membership for any role tier 1 has now covered.

    Member-level, not deletion, so it is reversible and visible — and so the
    scores it produced while it was the only grader keep pointing at it.
    """
    from overbae.services.eval.card_compiler import FLOOR_GENERATOR

    if not covered:
        return 0
    return EvalSetMember.objects.filter(
        eval_set__capability=capability,
        role__in=covered,
        enabled=True,
        evaluator__config__provenance__generator=FLOOR_GENERATOR,
    ).update(enabled=False)


def generate_and_preload_default_set(
    capability: Capability, *, created_by=None, time_budget_s: float | None = None
) -> dict:
    """Author the capability's bespoke eval suite and additively merge it into the
    Default set, partitioned by role applicability.

    A code RE-SCAN must not pile up graders. Deterministic specs are deduped by
    identity in ``_merge_specs_into_set``; tier 1 cannot be, so it is skipped
    outright once the capability has authored judges (see :func:`_already_authored`).
    """
    # Local imports keep this module out of the heavy generation + grounding
    # import graph and its cycle through the API layer.
    from overbae.services.eval import card_compiler, semantic_recommender
    from overbae.services.eval.grounding import resolve_grounding_for_capability

    eval_set = ensure_default_eval_set(capability, created_by=created_by)

    grounding = resolve_grounding_for_capability(capability)
    # Persisted here; later compiles read the stored value so routing stays stable.
    construct = card_compiler.resolve_construct(grounding, persist=True)
    tier0 = card_compiler.compile_card_evaluators(grounding)
    managed_card = card_compiler.compile_managed_card_evaluators(grounding)
    # Authoring is shown what is checked EXACTLY, not what still needs creating —
    # tier0 is provenance-deduped, so past the first scan it hides the checks a
    # judge would re-author.
    exact_coverage = card_compiler.deterministic_coverage(grounding)
    reauthored = not _already_authored(capability)
    drops: list[dict] = []
    budget_kwargs = {"time_budget_s": time_budget_s} if time_budget_s is not None else {}
    if reauthored:
        tier1, suites_timed_out, allocation = semantic_recommender.author_tier1_suites(
            grounding,
            exact_coverage,
            matrix_hint=_scan_matrix_hint(capability),
            drops=drops,
            **budget_kwargs,
        )
    else:
        tier1, suites_timed_out, allocation = [], [], None
    from overbae.services.eval.roles import roles_for_spec

    # Tier 1 authors generative judges only. Trace scoring is Tier 0 +
    # behaviour judges — an empty generative suite is the only floor case.
    empty_suites = (
        sorted({"generative"} - {role for spec in tier1 for role in roles_for_spec(spec)})
        if reauthored
        else []
    )
    floor = card_compiler.compile_floor_judge(grounding, roles=empty_suites) if empty_suites else []
    if reauthored:
        # The floor exists only because generative authoring produced nothing.
        # Once it has, leaving both standing would double-count the same card
        # failure modes.
        _retire_floor_judge(
            capability, covered={role for spec in tier1 for role in roles_for_spec(spec)}
        )
    # Appended UN-deduped: provenance dedup would skip backfilling a missing
    # membership on re-scan; the merge dedups by (name, scope) anyway.
    all_specs = tier0 + tier1 + managed_card + floor
    if not all_specs:
        logger.info(
            "[eval-preload] capability %s produced no specs (no grounding card)", capability.id
        )
        return {
            "eval_set_id": str(eval_set.id),
            "generated": 0,
            "created": 0,
            "added": 0,
            "empty_tier1_suites": empty_suites,
            "tier1_authored": reauthored,
            "suites_timed_out": suites_timed_out,
            "authoring_drops": len(drops),
            "construct": construct,
            "allocation": allocation,
            "floor_judge": False,
        }

    from overbae.services.eval.binding_check import validate_specs_on_synthetic_row

    synthetic_failures = validate_specs_on_synthetic_row(all_specs, grounding.codebase_card)
    failed_names = {f["name"] for f in synthetic_failures}
    if failed_names:
        all_specs = [s for s in all_specs if s.name not in failed_names]

    from overbae.services.eval.binding_check import restore_sweep_quarantined

    restore_sweep_quarantined(capability=capability)
    result = _merge_specs_into_set(capability, eval_set, all_specs, created_by=created_by)
    _drop_superseded_incumbent_members(eval_set, allocation)
    _drop_behaviour_generative_members(eval_set)
    _reconcile_compiler_memberships(eval_set, all_specs)
    coverage = _enforce_behaviour_coverage(capability, eval_set, grounding)
    _persist_authoring_status(
        capability,
        drops=drops,
        suites_timed_out=suites_timed_out,
        synthetic_failures=synthetic_failures,
        allocation=allocation,
    )
    total_added = result["generative"] + result["trace_scoring"]
    logger.info(
        "[eval-preload] capability %s merged Default set: %d generated, %d new evaluator(s), "
        "%d new member(s) (%d generative + %d trace_scoring)",
        capability.id,
        len(all_specs),
        result["created"],
        total_added,
        result["generative"],
        result["trace_scoring"],
    )
    if empty_suites:
        logger.error(
            "[eval-preload] capability %s authored NOTHING for tier-1 suite(s): %s",
            capability.id,
            ", ".join(empty_suites),
        )
    return {
        "eval_set_id": str(eval_set.id),
        "generated": len(all_specs),
        "created": result["created"],
        "added": total_added,
        "generative": result["generative"],
        "trace_scoring": result["trace_scoring"],
        "empty_tier1_suites": empty_suites,
        "tier1_authored": reauthored,
        "suites_timed_out": suites_timed_out,
        "authoring_drops": len(drops),
        "synthetic_failures": len(synthetic_failures),
        "coverage_complete": coverage.get("complete"),
        "construct": construct,
        "allocation": allocation,
        "floor_judge": bool(floor),
    }
