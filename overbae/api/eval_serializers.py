from __future__ import annotations

from collections import defaultdict

from django.db.models import Avg, Count, FloatField, Q, Sum
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from overbae.api.serializers import _user_project_ids
from overbae.models import (
    Annotation,
    Behaviour,
    Capability,
    Cell,
    EvalRun,
    EvalSample,
    EvalSet,
    EvalSetMember,
    Evaluator,
    EvalVariant,
    Project,
    RunEvaluator,
    Score,
    Verdict,
)
from overbae.services.datasets import use
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.eval import evidence
from overbae.services.model_catalog import is_model_available


def _eval_set_has_gold_comparator(eval_set: EvalSet) -> bool:
    for member in eval_set.members.filter(enabled=True).select_related("evaluator"):
        evaluator = member.evaluator
        if evaluator is None or evaluator.is_archived:
            continue
        if evaluator.kind == Evaluator.Kind.DETERMINISTIC:
            check = str((evaluator.config or {}).get("check") or "")
            if check in {"exact_match", "reference_field_compare"}:
                return True
        if evaluator.kind != Evaluator.Kind.LLM_JUDGE:
            continue
        if not evaluator.requires_reference:
            continue
        from overbae.services.eval.surface_binding import is_gold_comparator_claim

        for item in evaluator.checklist or []:
            if isinstance(item, dict) and is_gold_comparator_claim(str(item.get("q") or "")):
                return True
    return False


def _guard_closed_form_gold_coverage(*, eval_set: EvalSet, dataset) -> None:
    from overbae.services.eval.card_compiler import generate_observes_tool_calls
    from overbae.services.eval.grounding import resolve_grounding
    from overbae.services.eval.profiler import dataset_has_closed_form_reference
    from overbae.services.eval.surface_binding import uncovered_gold_comparator_claims

    if not dataset_has_closed_form_reference(dataset):
        return
    if _eval_set_has_gold_comparator(eval_set):
        return
    capability = getattr(dataset, "capability", None)
    ctx = resolve_grounding(dataset)
    card = ctx.codebase_card or (
        (getattr(capability, "improvement_metadata", None) or {}).get("capability_card")
        if capability is not None
        else None
    )
    checklists = [
        list(member.evaluator.checklist or [])
        for member in eval_set.members.filter(enabled=True).select_related("evaluator")
        if member.evaluator
        and not member.evaluator.is_archived
        and member.evaluator.kind == Evaluator.Kind.LLM_JUDGE
    ]
    uncovered = uncovered_gold_comparator_claims(
        card,
        checklists,
        generate_observes_tools=generate_observes_tool_calls(ctx),
        closed_form_reference=True,
    )
    if not uncovered:
        return
    names = "; ".join(uncovered[:5])
    if len(uncovered) > 5:
        names = f"{names}; …"
    raise serializers.ValidationError(
        {
            "eval_set": (
                "This eval set has no gold comparator for a closed-form dataset. "
                f"Uncovered card claims: {names}. Re-sync the Default set or attach "
                "a reference-based evaluator."
            )
        }
    )


def _require_membership(serializer, project):
    if project is None:
        return project
    user = serializer.context["request"].user
    if project.id not in _user_project_ids(user):
        # 403, not a 400 field error: this is authorization, and the rest of the
        # API answers the same condition with PermissionDenied.
        raise PermissionDenied("You are not a member of this project.")
    return project


class EvaluatorSerializer(serializers.ModelSerializer):
    checklist = serializers.JSONField(required=False)
    choices = serializers.JSONField(required=False)
    variable_mapping = serializers.JSONField(required=False)
    config = serializers.JSONField(required=False)
    # Denormalised so the detail view can label bespoke vs generic in one fetch.
    capability_name = serializers.CharField(source="capability.name", read_only=True, default=None)
    is_generic = serializers.SerializerMethodField()
    # The exact instruction text the judge is given, per-sample inputs left as
    # placeholders. Empty for non-judge kinds.
    judge_prompt = serializers.SerializerMethodField()

    class Meta:
        model = Evaluator
        fields = "__all__"
        read_only_fields = ["id", "created_at", "created_by", "version"]
        # Must stay empty: perform_create bumps the version per save, and DRF's
        # implicit (project, name, version) validator would reject the second one.
        validators: list = []

    def get_is_generic(self, obj) -> bool:
        return obj.capability_id is None

    def get_judge_prompt(self, obj) -> str:
        if obj.kind not in ("llm_judge", "agentic"):
            return ""
        try:
            from overbae.services.eval.rubric_compiler import build_judge_prompt_display

            return build_judge_prompt_display(obj)
        except Exception:  # noqa: BLE001
            return ""

    def validate_project(self, value):
        return _require_membership(self, value)

    def validate(self, attrs):
        """Caught here rather than at run time: a judge scored from checklist
        verdicts needs items, and the author is the only one who can supply them."""
        merged = Evaluator(
            **{
                field: attrs.get(field, getattr(self.instance, field, None))
                for field in (
                    "kind",
                    "name",
                    "scope",
                    "config",
                    "surface",
                    "checklist",
                    "variable_mapping",
                )
            },
            evidence_requirement=attrs.get(
                "evidence_requirement",
                getattr(self.instance, "evidence_requirement", "") or "",
            ),
            requires_reference=bool(
                attrs.get("requires_reference", getattr(self.instance, "requires_reference", False))
            ),
            applicable_roles=attrs.get(
                "applicable_roles", getattr(self.instance, "applicable_roles", None) or []
            ),
        )
        if merged.requires_checklist() and not (merged.checklist or []):
            raise serializers.ValidationError(
                {
                    "checklist": (
                        "An LLM judge needs a compiled checklist — its score is the "
                        "weighted fraction of items that pass. Compile the rubric first "
                        "(POST /evaluators/compile-rubric/)."
                    )
                }
            )
        unbound = merged.unbound_checklist_variables()
        if unbound:
            raise serializers.ValidationError(
                {
                    "checklist": (
                        "Checklist items reference "
                        f"{', '.join('{{' + v + '}}' for v in unbound)}, which nothing "
                        "binds. The judge would be asked about evidence it is never "
                        "given. Add the variable to variable_mapping, or rewrite the "
                        "items against the variables it does bind."
                    )
                }
            )
        return attrs


def _evaluator_score_from_summary(summary: dict, name: str) -> float | None:
    """Collapse one evaluator's per-variant cells in a run rollup to a 0..100 score.

    Holds the *metric* fixed and averages across variants — the inverse axis to
    ``optimizer._variant_score_from_summary``. ``None`` when the metric never
    produced a real score in this run, so the caller skips it.
    """
    variants = (summary or {}).get("variants", {}) or {}
    values: list[float] = []
    for variant in variants.values():
        cell = (variant.get("metrics") or {}).get(name)
        if not cell:
            continue
        mean = cell.get("mean")
        if mean is not None:
            values.append(mean * 100)
            continue
        pass_rate = cell.get("pass_rate")
        if pass_rate is not None:
            values.append(pass_rate * 100)
    return sum(values) / len(values) if values else None


def resolve_evaluator_run_scores(capability_ids) -> dict[tuple[str, str], dict]:
    """Per-(capability, evaluator-name) latest + previous completed-run score and delta.

    One query for every completed run of the given capabilities; a run belongs to an
    capability via ``dataset.capability``, and its stored ``summary`` already carries the
    aggregates, so nothing is recomputed from raw ``Score`` rows. Identity is the
    evaluator NAME, so a re-authored version still lines up with its history.
    """
    if not capability_ids:
        return {}
    runs = (
        EvalRun.objects.filter(
            dataset__capability_id__in=list(capability_ids), status=EvalRun.Status.COMPLETED
        )
        .order_by("-created_at")
        .values("id", "created_at", "summary", "dataset__capability_id")
    )
    # (capability_id, name) -> [(run_row, score), ...] most-recent first.
    by_key: dict[tuple[str, str], list[tuple[dict, float]]] = defaultdict(list)
    for run in runs:
        capability_id = str(run["dataset__capability_id"])
        summary = run["summary"] or {}
        for name in summary.get("metrics") or []:
            score = _evaluator_score_from_summary(summary, name)
            if score is not None:
                by_key[(capability_id, name)].append((run, round(score, 2)))

    result: dict[tuple[str, str], dict] = {}
    for key, entries in by_key.items():
        latest_run, latest_score = entries[0]
        prev_run, prev_score = entries[1] if len(entries) > 1 else (None, None)
        result[key] = {
            "latest": latest_score,
            "previous": prev_score,
            "delta": round(latest_score - prev_score, 2) if prev_score is not None else None,
            "latest_run_id": str(latest_run["id"]),
            "previous_run_id": str(prev_run["id"]) if prev_run else None,
            "latest_run_at": latest_run["created_at"],
            "previous_run_at": prev_run["created_at"] if prev_run else None,
        }
    return result


def resolve_evaluator_score_history(capability_id) -> list[dict]:
    """Per-evaluator-name time series of completed-run scores for one capability.

    One query, oldest first, reusing each run's stored ``summary`` rather than
    raw ``Score`` rows. Identity is the evaluator NAME; ``score`` is normalized
    to 0–100.
    """
    if not capability_id:
        return []
    runs = (
        EvalRun.objects.filter(
            dataset__capability_id=capability_id, status=EvalRun.Status.COMPLETED
        )
        .order_by("created_at")
        .values("id", "created_at", "summary")
    )
    by_name: dict[str, list[dict]] = defaultdict(list)
    for run in runs:
        summary = run["summary"] or {}
        for name in summary.get("metrics") or []:
            score = _evaluator_score_from_summary(summary, name)
            if score is not None:
                by_name[name].append(
                    {
                        "run_id": str(run["id"]),
                        "run_at": run["created_at"],
                        "score": round(score, 2),
                    }
                )
    # Ascending order means the highest version wins the dict slot. Null when no
    # live evaluator of that name survives.
    name_to_id: dict[str, str] = {}
    for ev in (
        Evaluator.objects.filter(capability_id=capability_id)
        .order_by("version")
        .values("name", "id")
    ):
        name_to_id[ev["name"]] = str(ev["id"])
    return [
        {"name": name, "evaluator_id": name_to_id.get(name), "points": points}
        for name, points in sorted(by_name.items())
    ]


class EvaluatorScoreHistoryPointSerializer(serializers.Serializer):
    """``score`` is normalized to 0–100."""

    run_id = serializers.CharField()
    run_at = serializers.DateTimeField()
    score = serializers.FloatField()


class EvaluatorScoreHistorySerializer(serializers.Serializer):
    name = serializers.CharField()
    evaluator_id = serializers.CharField(allow_null=True)
    points = EvaluatorScoreHistoryPointSerializer(many=True)


class EvaluatorCatalogSerializer(serializers.ModelSerializer):
    """The score fields populate only when the view passes an ``evaluator_scores``
    map in context; they stay null for an evaluator that has never run."""

    capability_name = serializers.CharField(source="capability.name", read_only=True, default=None)
    is_generic = serializers.SerializerMethodField()
    provenance = serializers.SerializerMethodField()
    latest_score = serializers.SerializerMethodField()
    previous_score = serializers.SerializerMethodField()
    delta = serializers.SerializerMethodField()
    latest_run_id = serializers.SerializerMethodField()
    previous_run_id = serializers.SerializerMethodField()
    latest_run_at = serializers.SerializerMethodField()
    previous_run_at = serializers.SerializerMethodField()
    applicable_roles = serializers.SerializerMethodField()

    class Meta:
        model = Evaluator
        fields = [
            "id",
            "name",
            "display_name",
            "version",
            "description",
            "kind",
            "scope",
            "score_type",
            "capability",
            "capability_name",
            "is_generic",
            "is_managed",
            "provenance",
            "applicable_roles",
            "latest_score",
            "previous_score",
            "delta",
            "latest_run_id",
            "previous_run_id",
            "latest_run_at",
            "previous_run_at",
        ]
        read_only_fields = fields

    def get_is_generic(self, obj) -> bool:
        return obj.capability_id is None

    @extend_schema_field(serializers.ListField(child=serializers.CharField()))
    def get_applicable_roles(self, obj) -> list[str]:
        from overbae.services.eval.roles import roles_for_evaluator

        return list(roles_for_evaluator(obj))

    @extend_schema_field(serializers.JSONField())
    def get_provenance(self, obj) -> dict:
        return (obj.config or {}).get("provenance") or {}

    def _run_score(self, obj) -> dict | None:
        # A run keys its summary by ``evaluator.name`` whether the evaluator is
        # capability-scoped or a shared library grader, so a capability-less evaluator
        # still resolves against the scoped capability's run history.
        capability_id = obj.capability_id or self.context.get("score_capability_id")
        if capability_id is None:
            return None
        return (self.context.get("evaluator_scores") or {}).get((str(capability_id), obj.name))

    def get_latest_score(self, obj) -> float | None:
        entry = self._run_score(obj)
        return entry["latest"] if entry else None

    def get_previous_score(self, obj) -> float | None:
        entry = self._run_score(obj)
        return entry["previous"] if entry else None

    def get_delta(self, obj) -> float | None:
        entry = self._run_score(obj)
        return entry["delta"] if entry else None

    def get_latest_run_id(self, obj) -> str | None:
        entry = self._run_score(obj)
        return entry["latest_run_id"] if entry else None

    def get_previous_run_id(self, obj) -> str | None:
        entry = self._run_score(obj)
        return entry["previous_run_id"] if entry else None

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_latest_run_at(self, obj):
        entry = self._run_score(obj)
        return entry["latest_run_at"] if entry else None

    @extend_schema_field(serializers.DateTimeField(allow_null=True))
    def get_previous_run_at(self, obj):
        entry = self._run_score(obj)
        return entry["previous_run_at"] if entry else None


class EvaluatorListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Evaluator
        fields = [
            "id",
            "project",
            "capability",
            "name",
            "display_name",
            "version",
            "description",
            "kind",
            "scope",
            "score_type",
            "is_managed",
            "is_archived",
            "created_at",
        ]
        read_only_fields = fields


class EvalVariantSerializer(serializers.ModelSerializer):
    params = serializers.JSONField(required=False)
    resolved_model = serializers.CharField(read_only=True)

    class Meta:
        model = EvalVariant
        fields = [
            "id",
            "run",
            "label",
            "model_ref",
            "model_name",
            "prompt",
            "params",
            "mode",
            "is_baseline",
            "order",
            "resolved_model",
            "created_at",
        ]
        read_only_fields = ["id", "run", "created_at", "resolved_model"]


class ScoreSerializer(serializers.ModelSerializer):
    sub_scores = serializers.JSONField(required=False)

    class Meta:
        model = Score
        fields = "__all__"
        read_only_fields = [f.name for f in Score._meta.fields]


class VerdictSerializer(serializers.ModelSerializer):
    """One evaluator's judgment of one target — the read path for every score
    detail surface. ``passed``/``scope``/``grain``/``gate``/``sub_scores`` are
    lifted out of ``metadata`` so consumers never parse it."""

    display_name = serializers.SerializerMethodField()
    passed = serializers.SerializerMethodField()
    scope = serializers.SerializerMethodField()
    grain = serializers.SerializerMethodField()
    gate = serializers.SerializerMethodField()
    sub_scores = serializers.SerializerMethodField()

    class Meta:
        model = Verdict
        fields = [
            "id",
            "evaluator",
            "evaluator_name",
            "display_name",
            "target_kind",
            "target_id",
            "label",
            "score",
            "passed",
            "outcome",
            "explanation",
            "unmet",
            "scope",
            "grain",
            "gate",
            "sub_scores",
            "annotator_kind",
            "identifier",
            "judge_trace_id",
            "cost",
            "latency_ms",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def _meta_dict(self, obj) -> dict:
        return obj.metadata if isinstance(obj.metadata, dict) else {}

    def get_display_name(self, obj) -> str:
        return (obj.evaluator.display_name or "") if obj.evaluator_id else ""

    @extend_schema_field(serializers.BooleanField(allow_null=True))
    def get_passed(self, obj) -> bool | None:
        passed = self._meta_dict(obj).get("passed")
        return passed if isinstance(passed, bool) else None

    def get_scope(self, obj) -> str:
        return str(self._meta_dict(obj).get("scope") or "")

    def get_grain(self, obj) -> str:
        return str(self._meta_dict(obj).get("grain") or "")

    def get_gate(self, obj) -> bool:
        return bool(self._meta_dict(obj).get("gate"))

    @extend_schema_field(serializers.ListField(child=serializers.JSONField()))
    def get_sub_scores(self, obj) -> list:
        sub_scores = self._meta_dict(obj).get("sub_scores")
        return sub_scores if isinstance(sub_scores, list) else []


class EvalSampleListSerializer(serializers.ModelSerializer):
    variant_label = serializers.CharField(source="variant.label", read_only=True)
    is_prepared = serializers.SerializerMethodField()
    input_preview = serializers.SerializerMethodField()
    output_preview = serializers.SerializerMethodField()

    class Meta:
        model = EvalSample
        fields = [
            "id",
            "run",
            "variant",
            "variant_label",
            "row_index",
            "source_trace_id",
            "context_coverage",
            "is_prepared",
            "input_preview",
            "output_preview",
            "error",
            "created_at",
        ]
        read_only_fields = fields

    def get_is_prepared(self, obj) -> bool:
        # Mirrors compute_run_progress's _PREPARED predicate.
        return bool(obj.trajectory) or bool(obj.error)

    def get_input_preview(self, obj) -> str:
        # The first user turn is the task text.
        messages = (obj.trajectory or {}).get("messages") or []
        text = next(
            (m.get("content") for m in messages if m.get("role") == "user" and m.get("content")),
            "",
        )
        return text[:240] if isinstance(text, str) else ""

    def get_output_preview(self, obj) -> str:
        traj = obj.trajectory or {}
        output = traj.get("final_output")
        if not output:
            messages = traj.get("messages") or []
            output = next(
                (
                    m.get("content")
                    for m in reversed(messages)
                    if m.get("role") == "assistant" and m.get("content")
                ),
                "",
            )
        if not isinstance(output, str):
            return ""
        return output[:240]


class EvalSampleSerializer(serializers.ModelSerializer):
    trajectory = serializers.JSONField(read_only=True)
    structured = serializers.JSONField(read_only=True)
    expected = serializers.JSONField(read_only=True)
    scores = ScoreSerializer(many=True, read_only=True)

    class Meta:
        model = EvalSample
        fields = [
            "id",
            "run",
            "variant",
            "row_index",
            "source_trace_id",
            "trajectory",
            "structured",
            "expected",
            "context_coverage",
            "error",
            "scores",
            "created_at",
        ]
        read_only_fields = fields


class AnnotationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Annotation
        fields = "__all__"
        read_only_fields = ["id", "created_at", "user", "project"]


class EvalRunVariantProgressSerializer(serializers.Serializer):
    id = serializers.CharField()
    label = serializers.CharField()
    total = serializers.IntegerField()
    prepared = serializers.IntegerField()
    errors = serializers.IntegerField()


class EvalRunEvaluatorStatSerializer(serializers.Serializer):
    """Partial while a run scores — these are rolling aggregates, not the rollup."""

    name = serializers.CharField()
    scored = serializers.IntegerField()
    mean = serializers.FloatField(allow_null=True)
    pass_rate = serializers.FloatField(allow_null=True)


class EvalRunProgressSerializer(serializers.Serializer):
    """``prepared/total`` tracks generation; ``scored/score_total`` tracks scoring
    at (sample × evaluator) granularity."""

    phase = serializers.ChoiceField(
        choices=[
            "pending",
            "generating",
            "scoring",
            "aggregating",
            "completed",
            "failed",
            "cancelled",
        ]
    )
    prepared = serializers.IntegerField()
    total = serializers.IntegerField()
    errors = serializers.IntegerField()
    scored = serializers.IntegerField()
    score_total = serializers.IntegerField()
    variants = EvalRunVariantProgressSerializer(many=True)
    evaluator_stats = EvalRunEvaluatorStatSerializer(many=True)


# EvalSample has no status column, so "prepared" is a written trajectory or a
# recorded error.
_PREPARED = ~Q(trajectory={}) | ~Q(error="")


def compute_run_progress(run) -> dict:
    """Four aggregate queries, never one per variant.

    Scores only appear after *every* sample finishes preparing (chord barrier),
    so a generating run must show movement through ``prepared``.
    """
    variant_rows = list(
        EvalSample.objects.filter(run=run)
        .values("variant_id", "variant__label")
        .annotate(
            total=Count("id"),
            prepared=Count("id", filter=_PREPARED),
            errors=Count("id", filter=~Q(error="")),
        )
        .order_by("variant__order", "variant__created_at")
    )
    variants = [
        {
            "id": str(r["variant_id"]),
            "label": r["variant__label"] or "",
            "total": r["total"],
            "prepared": r["prepared"],
            "errors": r["errors"],
        }
        for r in variant_rows
    ]
    total = sum(v["total"] for v in variants)
    prepared = sum(v["prepared"] for v in variants)
    errors = sum(v["errors"] for v in variants)

    # Scoring fans out per (sample × enabled evaluator), so distinct pairs are
    # what makes the bar move smoothly.
    evaluator_count = run.run_evaluators.filter(enabled=True).count()
    score_total = total * evaluator_count
    scored = (
        Score.objects.filter(run=run, sample__isnull=False)
        .values("sample_id", "run_evaluator_id")
        .distinct()
        .count()
    )

    stat_rows = (
        Score.objects.filter(run=run, sample__isnull=False)
        .exclude(name__endswith="__prediction")
        .values("name")
        .annotate(
            n=Count("id"),
            mean=Avg("value"),
            pass_count=Count("id", filter=Q(passed=True)),
            pass_total=Count("id", filter=Q(passed__isnull=False)),
        )
        .order_by("name")
    )
    evaluator_stats = [
        {
            "name": r["name"],
            "scored": r["n"],
            "mean": round(r["mean"], 4) if r["mean"] is not None else None,
            "pass_rate": round(r["pass_count"] / r["pass_total"], 4) if r["pass_total"] else None,
        }
        for r in stat_rows
    ]

    if run.status == EvalRun.Status.RUNNING:
        if not total or prepared < total:
            phase = "generating"
        elif score_total and scored >= score_total:
            phase = "aggregating"
        else:
            phase = "scoring"
    else:
        # pending / completed / failed / cancelled map 1:1.
        phase = run.status

    return {
        "phase": phase,
        "prepared": prepared,
        "total": total,
        "errors": errors,
        "scored": scored,
        "score_total": score_total,
        "variants": variants,
        "evaluator_stats": evaluator_stats,
    }


class EvalRunOperationalStatSerializer(serializers.Serializer):
    """``gen_*`` is the model-under-test's responses, ``eval_*`` the LLM-judge
    passes. Latency and token fields are null when the provider reported no
    timing or usage."""

    variant_id = serializers.CharField()
    label = serializers.CharField()
    sample_count = serializers.IntegerField()
    gen_cost = serializers.FloatField()
    eval_cost = serializers.FloatField()
    total_cost = serializers.FloatField()
    gen_latency_ms = serializers.FloatField(allow_null=True)
    eval_latency_ms = serializers.FloatField(allow_null=True)
    total_tokens = serializers.FloatField(allow_null=True)


def compute_operational_stats(run) -> list[dict]:
    """Two aggregate queries.

    Generation metrics are summed DB-side out of ``trajectory.metadata`` so no
    trajectory blob is ever loaded. Judge latency averages only the rows that
    actually timed an LLM call (``> 0``).
    """
    meta = KeyTextTransform("metadata", "trajectory")
    gen_rows = (
        EvalSample.objects.filter(run=run)
        .annotate(
            _gen_cost=Cast(KeyTextTransform("cost", meta), FloatField()),
            _gen_latency=Cast(KeyTextTransform("latency_ms", meta), FloatField()),
            _gen_tokens=Cast(KeyTextTransform("total_tokens", meta), FloatField()),
        )
        .values("variant_id")
        .annotate(
            sample_count=Count("id"),
            gen_cost=Sum("_gen_cost"),
            gen_latency_ms=Avg("_gen_latency"),
            total_tokens=Sum("_gen_tokens"),
        )
    )
    gen_by_variant = {str(r["variant_id"]): r for r in gen_rows}

    judge_rows = (
        Score.objects.filter(run=run, variant__isnull=False)
        .values("variant_id")
        .annotate(
            eval_cost=Sum("cost"),
            eval_latency_ms=Avg("latency_ms", filter=Q(latency_ms__gt=0)),
        )
    )
    judge_by_variant = {str(r["variant_id"]): r for r in judge_rows}

    stats: list[dict] = []
    for variant in run.variants.all().order_by("order", "created_at"):
        vid = str(variant.id)
        gen = gen_by_variant.get(vid, {})
        judge = judge_by_variant.get(vid, {})
        gen_cost = gen.get("gen_cost") or 0.0
        eval_cost = judge.get("eval_cost") or 0.0
        stats.append(
            {
                "variant_id": vid,
                "label": variant.label,
                "sample_count": gen.get("sample_count") or 0,
                "gen_cost": gen_cost,
                "eval_cost": eval_cost,
                "total_cost": gen_cost + eval_cost,
                "gen_latency_ms": gen.get("gen_latency_ms"),
                "eval_latency_ms": judge.get("eval_latency_ms"),
                "total_tokens": gen.get("total_tokens"),
            }
        )
    return stats


class EvalRunListSerializer(serializers.ModelSerializer):
    variant_count = serializers.SerializerMethodField()
    evaluator_count = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    dataset_name = serializers.CharField(source="dataset.name", read_only=True, default="")
    # Denormalised through the dataset; null for unassigned or trace-based runs.
    capability_name = serializers.CharField(
        source="dataset.capability.name", read_only=True, default=""
    )
    capability_id = serializers.UUIDField(
        source="dataset.capability_id", read_only=True, default=None
    )
    # Both come from EvalRunViewSet.get_queryset annotations, not the model.
    origin = serializers.CharField(read_only=True, default="manual")
    optimizer_experiment = serializers.UUIDField(read_only=True, allow_null=True, default=None)

    class Meta:
        model = EvalRun
        fields = [
            "id",
            "project",
            "name",
            "description",
            "data_source",
            "dataset",
            "dataset_name",
            "capability_name",
            "capability_id",
            "origin",
            "optimizer_experiment",
            "status",
            "error",
            "max_items",
            "sampling",
            "variant_count",
            "evaluator_count",
            "progress",
            "created_at",
            "completed_at",
        ]
        read_only_fields = fields

    def get_variant_count(self, obj) -> int:
        return obj.variants.count()

    def get_evaluator_count(self, obj) -> int:
        return obj.evaluators.count()

    @extend_schema_field(EvalRunProgressSerializer(allow_null=True))
    def get_progress(self, obj):
        # Skipped for terminal runs — this endpoint is polled and the counts
        # cost four aggregate queries per row.
        if obj.is_terminal:
            return None
        return compute_run_progress(obj)


class RunEvaluatorSerializer(serializers.ModelSerializer):
    snapshot = serializers.JSONField(read_only=True)
    name = serializers.CharField(read_only=True)
    kind = serializers.CharField(read_only=True)

    class Meta:
        model = RunEvaluator
        fields = [
            "id",
            "run",
            "evaluator",
            "name",
            "kind",
            "snapshot",
            "scope_override",
            "sampling",
            "enabled",
            "order",
            "created_at",
        ]
        read_only_fields = fields


def _dataset_reference_available(dataset) -> bool:
    """Suppresses the ``needs_reference`` warning when grounding vars will resolve
    from context. Cached by the grounding service."""
    if dataset is None:
        return False
    try:
        from overbae.services.eval.grounding import build_reference_context

        return bool(build_reference_context(dataset))
    except Exception:  # noqa: BLE001 — warnings are advisory, never fatal
        return False


class EvalRunWarningSerializer(serializers.Serializer):
    """Advisory only — a warning never blocks a run."""

    evaluator = serializers.CharField()
    variant = serializers.CharField(allow_blank=True)
    variant_mode = serializers.CharField(allow_blank=True)
    evidence_requirement = serializers.CharField()
    severity = serializers.CharField()
    message = serializers.CharField()


class EvalRunSerializer(serializers.ModelSerializer):
    variants = EvalVariantSerializer(many=True, read_only=True)
    run_evaluators = RunEvaluatorSerializer(many=True, read_only=True)
    trace_filter = serializers.JSONField(required=False)
    summary = serializers.JSONField(read_only=True)
    progress = serializers.SerializerMethodField()
    operational = serializers.SerializerMethodField()
    warnings = serializers.SerializerMethodField()
    # Denormalised through the dataset so the comparison view can match an
    # capability-mode cohort even when the run itself is datasetless.
    capability_id = serializers.CharField(
        source="dataset.capability_id", read_only=True, default=None
    )
    capability_name = serializers.CharField(
        source="dataset.capability.name", read_only=True, default=""
    )
    dataset_name = serializers.CharField(source="dataset.name", read_only=True, default="")
    # The cell the run read. Omitted on create → the dataset's active cell.
    cell = serializers.PrimaryKeyRelatedField(
        queryset=Cell.objects.all(), required=False, allow_null=True
    )
    cell_info = serializers.SerializerMethodField()
    # Each attached evaluator is snapshotted at attach time.
    evaluator_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Evaluator.objects.all(),
        write_only=True,
        required=False,
    )
    # ``[{"evaluator": <id>, "prompt": <id|null>}]``; ``prompt=null`` grades every
    # variant. One evaluator bound to two prompts yields two RunEvaluator rows.
    evaluator_bindings = serializers.ListField(
        child=serializers.DictField(),
        write_only=True,
        required=False,
    )
    # ``evaluator_ids`` / ``evaluator_bindings`` override the set: when either is
    # supplied the set is recorded for provenance but NOT expanded.
    eval_set = serializers.PrimaryKeyRelatedField(
        queryset=EvalSet.objects.all(),
        required=False,
        allow_null=True,
    )
    variants_input = serializers.ListField(
        child=serializers.DictField(), write_only=True, required=False
    )

    class Meta:
        model = EvalRun
        fields = [
            "id",
            "project",
            "name",
            "description",
            "data_source",
            "dataset",
            "capability_id",
            "capability_name",
            "trace_filter",
            "max_items",
            "sampling",
            "status",
            "error",
            "summary",
            "progress",
            "operational",
            "warnings",
            "variants",
            "run_evaluators",
            "evaluator_ids",
            "evaluator_bindings",
            "eval_set",
            "variants_input",
            "triggered_by",
            "dataset_name",
            "cell",
            "cell_info",
            "created_at",
            "updated_at",
            "completed_at",
        ]
        read_only_fields = [
            "id",
            "capability_id",
            "capability_name",
            "dataset_name",
            "cell_info",
            "status",
            "error",
            "summary",
            "progress",
            "operational",
            "warnings",
            "variants",
            "run_evaluators",
            "triggered_by",
            "created_at",
            "updated_at",
            "completed_at",
        ]

    def validate_project(self, value):
        return _require_membership(self, value)

    def get_cell_info(self, obj) -> dict | None:
        return use.describe(obj.cell if obj.cell_id else None)

    @extend_schema_field(EvalRunProgressSerializer(allow_null=True))
    def get_progress(self, obj):
        if obj.is_terminal:
            return None
        return compute_run_progress(obj)

    @extend_schema_field(EvalRunOperationalStatSerializer(many=True))
    def get_operational(self, obj):
        return compute_operational_stats(obj)

    @extend_schema_field(EvalRunWarningSerializer(many=True))
    def get_warnings(self, obj):
        evaluators = []
        for run_eval in obj.run_evaluators.filter(enabled=True):
            snap = run_eval.snapshot or {}
            requirement = snap.get("evidence_requirement") or evidence.infer_evidence_requirement(
                kind=snap.get("kind", "llm_judge"),
                scope=snap.get("scope", "final_output"),
                variable_mapping=snap.get("variable_mapping", []),
                requires_reference=snap.get("requires_reference", False),
                config=snap.get("config", {}),
            )
            evaluators.append(
                {
                    "name": snap.get("name", ""),
                    "evidence_requirement": requirement,
                    "requires_reference": snap.get("requires_reference", False),
                }
            )
        variant_modes = [{"label": v.label, "mode": v.mode} for v in obj.variants.all()]
        reference_available = _dataset_reference_available(obj.dataset)
        return evidence.compatibility_warnings(
            evaluators, variant_modes, reference_available=reference_available
        )

    def validate(self, attrs):
        data_source = attrs.get("data_source", EvalRun.DataSource.DATASET)
        dataset = attrs.get("dataset")
        if data_source == EvalRun.DataSource.DATASET and not dataset:
            raise serializers.ValidationError(
                {"dataset": "A dataset is required for dataset runs."}
            )
        if dataset is not None:
            project = attrs.get("project") or getattr(self.instance, "project", None)
            if project is not None and dataset.project_id != project.id:
                raise serializers.ValidationError(
                    {"dataset": "This dataset belongs to a different project."}
                )
            if self.instance is None or "dataset" in attrs or "cell" in attrs:
                try:
                    attrs["cell"] = use.check(dataset, "eval", cell=attrs.get("cell"))
                except DatasetError as exc:
                    raise serializers.ValidationError({"dataset": exc.detail}) from exc
        eval_set = attrs.get("eval_set")
        if (
            eval_set is not None
            and not attrs.get("evaluator_ids")
            and not attrs.get("evaluator_bindings")
        ):
            from overbae.services.eval.eval_set import active_members

            modes = {
                str(v.get("mode", EvalVariant.Mode.EXISTING))
                for v in (attrs.get("variants_input") or [])
            }
            role = (
                EvalSetMember.Role.TRACE_SCORING
                if modes and modes <= {EvalVariant.Mode.EXISTING}
                else EvalSetMember.Role.GENERATIVE
            )
            if role == EvalSetMember.Role.GENERATIVE:
                members = list(active_members(eval_set, role))
                if not any(m.evaluator.kind == Evaluator.Kind.LLM_JUDGE for m in members):
                    raise serializers.ValidationError(
                        {
                            "eval_set": "This eval set has no live generative judges. "
                            "Re-author the suite or attach judges explicitly."
                        }
                    )
            if dataset is not None:
                _guard_closed_form_gold_coverage(eval_set=eval_set, dataset=dataset)
        return attrs

    def validate_eval_set(self, value):
        if value is not None:
            _require_membership(self, value.project)
        return value

    def validate_variants_input(self, value):
        # Only OpenRouter-routed generate variants are checked: existing mode
        # replays ingested traces and model_ref points at a registered endpoint.
        # Rejecting here beats failing every sample at execution.
        for variant in value:
            mode = str(variant.get("mode", EvalVariant.Mode.EXISTING))
            if mode != EvalVariant.Mode.GENERATE or variant.get("model_ref"):
                continue
            model_name = (variant.get("model_name") or "").strip()
            if not model_name:
                raise serializers.ValidationError(
                    "Generate-mode variants require a model_name or model_ref."
                )
            if not is_model_available(model_name):
                raise serializers.ValidationError(
                    f"Model '{model_name}' is not available on OpenRouter — "
                    "pick a model from the catalog."
                )
        return value

    def create(self, validated_data):
        from overbae.services.eval import snapshots
        from overbae.services.eval.eval_set import expand_to_run_evaluators

        evaluators = validated_data.pop("evaluator_ids", [])
        bindings = validated_data.pop("evaluator_bindings", [])
        variants_input = validated_data.pop("variants_input", [])
        run = EvalRun.objects.create(**validated_data)
        use.freeze(run.cell)

        # Flat path: each evaluator is global (prompt=null) and grades every variant.
        order = 0
        for evaluator in evaluators:
            RunEvaluator.objects.create(
                run=run,
                evaluator=evaluator,
                snapshot=snapshots.build_snapshot(evaluator),
                order=order,
            )
            order += 1

        # Per-prompt path: one RunEvaluator per binding, each with its own snapshot.
        if bindings:
            evaluator_ids = {str(b.get("evaluator")) for b in bindings if b.get("evaluator")}
            by_id = {str(e.id): e for e in Evaluator.objects.filter(id__in=evaluator_ids)}
            for binding in bindings:
                evaluator = by_id.get(str(binding.get("evaluator")))
                if evaluator is None:
                    continue
                RunEvaluator.objects.create(
                    run=run,
                    evaluator=evaluator,
                    snapshot=snapshots.build_snapshot(evaluator),
                    prompt_id=binding.get("prompt") or None,
                    order=order,
                )
                order += 1

        # EvalSet path: expand only the members whose role matches the run's
        # intent, so the two launch paths never borrow each other's set.
        if run.eval_set_id and not evaluators and not bindings:
            from overbae.models import EvalSetMember

            modes = {v.get("mode", EvalVariant.Mode.EXISTING) for v in variants_input}
            # Trace-scoring only when EVERY variant is a replay; anything else
            # keeps the generative set, so a generation run is never starved of
            # graders.
            role = (
                EvalSetMember.Role.TRACE_SCORING
                if modes and modes <= {EvalVariant.Mode.EXISTING}
                else EvalSetMember.Role.GENERATIVE
            )
            expand_to_run_evaluators(run, run.eval_set, role=role)
        for i, v in enumerate(variants_input):
            EvalVariant.objects.create(
                run=run,
                label=v.get("label", f"variant_{i}"),
                model_ref_id=v.get("model_ref"),
                model_name=v.get("model_name", ""),
                prompt_id=v.get("prompt"),
                params=v.get("params", {}) or {},
                mode=v.get("mode", EvalVariant.Mode.EXISTING),
                is_baseline=bool(v.get("is_baseline", i == 0)),
                order=v.get("order", i),
            )
        return run


class EvalSetMemberSerializer(serializers.ModelSerializer):
    """The score fields are 0–100 over the two most recent completed runs, and
    populate only when the view passes an ``evaluator_scores`` map in context —
    the same resolver the catalog uses, so the numbers line up."""

    evaluator_name = serializers.CharField(source="evaluator.name", read_only=True)
    evaluator_display_name = serializers.CharField(source="evaluator.display_name", read_only=True)
    evaluator_kind = serializers.CharField(source="evaluator.kind", read_only=True)
    latest_score = serializers.SerializerMethodField()
    previous_score = serializers.SerializerMethodField()
    delta = serializers.SerializerMethodField()

    class Meta:
        model = EvalSetMember
        fields = [
            "id",
            "eval_set",
            "evaluator",
            "evaluator_name",
            "evaluator_display_name",
            "evaluator_kind",
            "role",
            "enabled",
            "sampling_rate",
            "prompt",
            "order",
            "created_at",
            "latest_score",
            "previous_score",
            "delta",
        ]
        read_only_fields = ["id", "eval_set", "created_at"]

    def _run_score(self, obj) -> dict | None:
        # Identity is (capability that OWNS this set, evaluator name), never the
        # evaluator's own capability: a run summary keys metrics by name, so keying by
        # ``evaluator.capability_id`` blanks every generic member that did score.
        evaluator = obj.evaluator
        if evaluator is None:
            return None
        capability_id = obj.eval_set.capability_id
        if capability_id is None:
            return None
        return (self.context.get("evaluator_scores") or {}).get(
            (str(capability_id), evaluator.name)
        )

    def get_latest_score(self, obj) -> float | None:
        entry = self._run_score(obj)
        return entry["latest"] if entry else None

    def get_previous_score(self, obj) -> float | None:
        entry = self._run_score(obj)
        return entry["previous"] if entry else None

    def get_delta(self, obj) -> float | None:
        entry = self._run_score(obj)
        return entry["delta"] if entry else None


class EvalSetSerializer(serializers.ModelSerializer):
    members = EvalSetMemberSerializer(many=True, read_only=True)
    is_active = serializers.SerializerMethodField()
    generative_count = serializers.SerializerMethodField()
    trace_scoring_count = serializers.SerializerMethodField()

    class Meta:
        model = EvalSet
        fields = [
            "id",
            "project",
            "capability",
            "name",
            "description",
            "prompts",
            "is_active",
            "generative_count",
            "trace_scoring_count",
            "members",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_by",
            "created_at",
            "updated_at",
        ]

    def validate_project(self, value):
        return _require_membership(self, value)

    def get_is_active(self, obj) -> bool:
        return obj.capability.active_eval_set_id == obj.id

    def get_generative_count(self, obj) -> int:
        return sum(
            1 for m in obj.members.all() if m.role == EvalSetMember.Role.GENERATIVE and m.enabled
        )

    def get_trace_scoring_count(self, obj) -> int:
        return sum(
            1 for m in obj.members.all() if m.role == EvalSetMember.Role.TRACE_SCORING and m.enabled
        )


class EvalSetAddMembersRequestSerializer(serializers.Serializer):
    """``specs`` are materialized as capability-scoped rows; ``evaluator_ids`` link
    existing library rows. Both may be given in one batch."""

    role = serializers.ChoiceField(
        choices=[EvalSetMember.Role.GENERATIVE, EvalSetMember.Role.TRACE_SCORING],
        default=EvalSetMember.Role.GENERATIVE,
    )
    specs = serializers.ListField(
        child=serializers.DictField(),
        required=False,
        default=list,
        help_text="EvaluatorSpec payloads (as returned by the generate-evals endpoint).",
    )
    evaluator_ids = serializers.PrimaryKeyRelatedField(
        many=True,
        queryset=Evaluator.objects.all(),
        required=False,
        default=list,
        help_text="Existing library evaluator ids to link as members.",
    )


class EvalSetMemberUpdateSerializer(serializers.Serializer):
    role = serializers.ChoiceField(
        choices=[EvalSetMember.Role.GENERATIVE, EvalSetMember.Role.TRACE_SCORING],
        required=False,
    )
    enabled = serializers.BooleanField(required=False)
    order = serializers.IntegerField(required=False, min_value=0)


class AuthorJudgeEvaluatorRequestSerializer(serializers.Serializer):
    """Maps the three score-type UIs onto one runnable ``llm_judge`` Evaluator."""

    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    capability = serializers.PrimaryKeyRelatedField(
        queryset=Capability.objects.all(), required=False, allow_null=True
    )
    name = serializers.CharField(max_length=255)
    judge_model = serializers.CharField(required=False, allow_blank=True, default="")
    evaluation_prompt = serializers.CharField()
    score_type = serializers.ChoiceField(choices=["numeric", "boolean", "categorical"])
    score_reasoning_prompt = serializers.CharField(required=False, allow_blank=True, default="")
    # Numeric
    score_output_prompt = serializers.CharField(required=False, allow_blank=True, default="")
    # Boolean
    boolean_verdict_prompt = serializers.CharField(required=False, allow_blank=True, default="")
    # Categorical
    categories = serializers.ListField(
        child=serializers.CharField(allow_blank=True), required=False, default=list
    )
    allow_multiple = serializers.BooleanField(required=False, default=False)
    category_selection_prompt = serializers.CharField(required=False, allow_blank=True, default="")
    # Omitted or [] derives the role from scope; when given it must name one.
    applicable_roles = serializers.ListField(
        child=serializers.ChoiceField(choices=["generative", "trace_scoring"]),
        required=False,
        default=list,
        allow_empty=False,
    )
    # Task-scoped authoring: required when trace_scoring is among applicable_roles.
    behaviour = serializers.PrimaryKeyRelatedField(
        queryset=Behaviour.objects.all(), required=False, allow_null=True
    )
    behaviour_role = serializers.ChoiceField(
        choices=["outcome", "step"], required=False, default="outcome"
    )
    anchor_segment = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )
    # Attach-on-save: skips a second round-trip through .../members/.
    eval_set = serializers.PrimaryKeyRelatedField(
        queryset=EvalSet.objects.all(), required=False, allow_null=True
    )
    eval_set_role = serializers.ChoiceField(
        choices=["generative", "trace_scoring"], required=False, allow_null=True, default=None
    )

    def validate_project(self, value):
        return _require_membership(self, value)

    def validate_applicable_roles(self, value):
        return list(dict.fromkeys(value))

    def validate_evaluation_prompt(self, value):
        if not value.strip():
            raise serializers.ValidationError("The evaluation prompt cannot be empty.")
        return value

    def validate(self, attrs):
        capability = attrs.get("capability")
        behaviour = attrs.get("behaviour")
        if behaviour is not None:
            if behaviour.project_id != attrs["project"].id:
                raise serializers.ValidationError(
                    {"behaviour": "Behaviour does not belong to this project."}
                )
            # A task-scoped judge always lands on the owning capability, never generic.
            if capability is None:
                capability = behaviour.capability
                attrs["capability"] = capability
            elif capability.id != behaviour.capability_id:
                raise serializers.ValidationError(
                    {"behaviour": "Behaviour belongs to a different capability."}
                )
        if capability is not None and capability.project_id != attrs["project"].id:
            raise serializers.ValidationError(
                {"capability": "Capability does not belong to this project."}
            )
        if attrs["score_type"] == "categorical":
            cats = [c.strip() for c in attrs.get("categories", []) if c and c.strip()]
            if len(cats) < 2:
                raise serializers.ValidationError(
                    {"categories": "Provide at least 2 categories — they must be exhaustive."}
                )
            if len({c.lower() for c in cats}) != len(cats):
                raise serializers.ValidationError({"categories": "Categories must be unique."})
            attrs["categories"] = cats
        if behaviour is not None and attrs.get("behaviour_role") == "step":
            from overbae.services.behaviour.contract import anchor_segment_valid

            segment = [a for a in attrs.get("anchor_segment") or [] if str(a).strip()]
            if not segment:
                raise serializers.ValidationError(
                    {"anchor_segment": "A step judge needs at least one anchor."}
                )
            version = behaviour.versions.order_by("-created_at").first()
            if version is None:
                raise serializers.ValidationError(
                    {"behaviour": "This task has no analyzed version yet."}
                )
            if not anchor_segment_valid(segment, version.contract or {}):
                raise serializers.ValidationError(
                    {"anchor_segment": "This segment does not appear in the task's contract."}
                )
            attrs["anchor_segment"] = segment
        eval_set = attrs.get("eval_set")
        if eval_set is not None:
            if eval_set.project_id != attrs["project"].id:
                raise serializers.ValidationError(
                    {"eval_set": "Eval set does not belong to this project."}
                )
            if not attrs.get("eval_set_role"):
                raise serializers.ValidationError(
                    {"eval_set_role": "Choose generative or trace_scoring to attach on save."}
                )
        roles = attrs.get("applicable_roles") or []
        if "trace_scoring" in roles and behaviour is None:
            raise serializers.ValidationError(
                {"behaviour": "Trace scoring evaluators must be bound to a task."}
            )
        return attrs


class GenerateEvaluatorPromptRequestSerializer(serializers.Serializer):
    description = serializers.CharField()
    capability = serializers.PrimaryKeyRelatedField(
        queryset=Capability.objects.all(), required=False, allow_null=True
    )
    # generative is reference-grounded authoring, trace_scoring has no gold.
    applicable_role = serializers.ChoiceField(
        choices=["generative", "trace_scoring"],
        required=False,
        default="generative",
    )

    def validate_description(self, value):
        if not value.strip():
            raise serializers.ValidationError("Describe what you want to evaluate.")
        return value


class GenerateEvaluatorPromptResponseSerializer(serializers.Serializer):
    prompt = serializers.CharField()
    grounded = serializers.BooleanField()
    score_type = serializers.ChoiceField(choices=["numeric", "boolean", "categorical"])
    score_reasoning_prompt = serializers.CharField(allow_blank=True)
    score_output_prompt = serializers.CharField(allow_blank=True)
    boolean_verdict_prompt = serializers.CharField(allow_blank=True)
    categories = serializers.ListField(child=serializers.CharField())
    allow_multiple = serializers.BooleanField()
    category_selection_prompt = serializers.CharField(allow_blank=True)
