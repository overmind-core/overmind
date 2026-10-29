from __future__ import annotations

import uuid

from django.db import transaction
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.exceptions import PermissionDenied

from overbae.api.serializers import _user_project_ids
from overbae.core.model_registry import judge_picker_models
from overbae.innermind import context, deterministic, generation, runs
from overbae.innermind.definitions import DefinitionSource, definition_source
from overbae.models import (
    Cell,
    Dataset,
    EvalMatrix,
    EvalRun,
    EvalRunRow,
    EvalScore,
    EvalSet,
    ModelRef,
    Project,
)
from overbae.services.activity import asset, record
from overbae.services.datasets import rows as row_store
from overbae.services.datasets import use
from overbae.services.datasets.lifecycle import DatasetError
from overbae.services.model_catalog import is_model_available

MAX_TARGETS = 6
EXPECTATION_MAX_CHARS = 1_000


def require_membership(serializer, project):
    if project is None:
        return project
    if project.id not in _user_project_ids(serializer.context["request"].user):
        raise PermissionDenied("You are not a member of this project.")
    return project


def require_same_project(project, **objects) -> None:
    for field, obj in objects.items():
        if obj is not None and obj.project_id != project.pk:
            raise serializers.ValidationError(
                {field: "This resource belongs to a different project."}
            )


class EvalMatrixSerializer(serializers.ModelSerializer):
    definition_source = serializers.SerializerMethodField()

    @transaction.atomic
    def create(self, validated_data):

        row = super().create(validated_data)
        assets = [asset("evaluation", row.pk, row.name)]
        if row.capability_id:
            assets.append(asset("capability", row.capability_id, row.capability.name))
        record(
            key=f"evaluation:{row.pk}",
            project_id=row.project_id,
            user_id=row.created_by_id,
            kind="evaluation_added",
            action="Added an evaluation",
            assets=assets,
        )
        return row

    def get_definition_source(self, obj) -> DefinitionSource:
        return definition_source(obj)

    class Meta:
        model = EvalMatrix
        fields = [
            "id",
            "project",
            "capability",
            "name",
            "type",
            "source",
            "definition_source",
            "span_type",
            "span_name",
            "enabled",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def validate_project(self, value):
        return require_membership(self, value)

    def validate(self, attrs):
        require_same_project(attrs["project"], capability=attrs.get("capability"))
        source = attrs["source"].strip()
        if not source:
            raise serializers.ValidationError({"source": "Required."})
        if attrs["type"] == EvalMatrix.Type.DETERMINISTIC:
            problem = deterministic.audit(source)
            if problem:
                raise serializers.ValidationError({"source": problem})
        elif len(source) > EXPECTATION_MAX_CHARS:
            raise serializers.ValidationError(
                {"source": f"An expectation is at most {EXPECTATION_MAX_CHARS} characters."}
            )
        attrs["source"] = source
        return attrs


class EvalSetSerializer(serializers.ModelSerializer):
    rows = serializers.PrimaryKeyRelatedField(
        many=True, queryset=EvalMatrix.objects.all(), required=False
    )
    is_active = serializers.SerializerMethodField()

    class Meta:
        model = EvalSet
        fields = [
            "id",
            "project",
            "capability",
            "name",
            "description",
            "rows",
            "is_active",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "is_active", "created_at", "updated_at"]

    def validate_project(self, value):
        return require_membership(self, value)

    def get_is_active(self, obj) -> bool:
        return bool(obj.capability_id and obj.capability.active_eval_set_id == obj.id)

    def validate(self, attrs):
        project = attrs.get("project") or self.instance.project
        capability = attrs.get("capability", self.instance.capability if self.instance else None)
        require_same_project(project, capability=capability)
        for row in attrs.get("rows", []):
            if row.project_id != project.pk or row.capability_id not in (
                None,
                capability and capability.pk,
            ):
                raise serializers.ValidationError(
                    {"rows": f"'{row.name}' belongs to a different project or capability."}
                )
        return attrs


class EvaluationContextSuggestionSerializer(serializers.Serializer):
    model = serializers.CharField()
    name = serializers.CharField()
    context_window = serializers.IntegerField()
    max_output_tokens = serializers.IntegerField()
    reserved_output_tokens = serializers.IntegerField()
    estimated_cost_usd = serializers.FloatField(allow_null=True)
    cost_delta_usd = serializers.FloatField(allow_null=True)


class EvaluationContextCheckSerializer(serializers.Serializer):
    role = serializers.CharField()
    label = serializers.CharField()
    model = serializers.CharField(allow_blank=True)
    context_window = serializers.IntegerField(allow_null=True)
    max_output_tokens = serializers.IntegerField(allow_null=True)
    estimated_input_tokens = serializers.IntegerField()
    total_input_tokens = serializers.IntegerField(default=0)
    configured_model = serializers.CharField(default="", allow_blank=True)
    reserved_output_tokens = serializers.IntegerField()
    required_context = serializers.IntegerField()
    checked_rows = serializers.IntegerField()
    affected_rows = serializers.IntegerField()
    row_indices = serializers.ListField(child=serializers.IntegerField())
    estimated = serializers.BooleanField()
    status = serializers.CharField()
    message = serializers.CharField(allow_blank=True)
    estimated_cost_usd = serializers.FloatField(allow_null=True, default=None)
    cost_basis = serializers.CharField(allow_blank=True, default="")
    suggestions = EvaluationContextSuggestionSerializer(many=True, default=list)
    suggestion_note = serializers.CharField(allow_blank=True, default="")


class EvaluationJudgeOptionSerializer(EvaluationContextSuggestionSerializer):
    status = serializers.CharField()
    context_window = serializers.IntegerField(allow_null=True)
    max_output_tokens = serializers.IntegerField(allow_null=True)


class EvaluationContextReportSerializer(serializers.Serializer):
    checks = EvaluationContextCheckSerializer(many=True)
    judge_models = EvaluationJudgeOptionSerializer(many=True)


class EvalRunTargetSerializer(serializers.Serializer):
    label = serializers.CharField(required=False, allow_blank=True, max_length=255)
    mode = serializers.ChoiceField(
        choices=[EvalRun.Mode.RECORDED, EvalRun.Mode.GENERATE], default=EvalRun.Mode.GENERATE
    )
    model_name = serializers.CharField(required=False, allow_blank=True, max_length=512)
    model_ref = serializers.PrimaryKeyRelatedField(
        queryset=ModelRef.objects.all(), required=False, allow_null=True
    )
    params = serializers.DictField(required=False)
    is_baseline = serializers.BooleanField(default=False)
    output_tokens = serializers.IntegerField(required=False, min_value=1, max_value=1_000_000)

    def validate(self, attrs):
        if attrs["mode"] != EvalRun.Mode.GENERATE:
            return attrs
        if attrs.get("model_ref"):
            return attrs
        model_name = (attrs.get("model_name") or "").strip()
        if not model_name:
            raise serializers.ValidationError("A generate target needs a model_name or model_ref.")
        if not is_model_available(model_name):
            raise serializers.ValidationError(
                f"Model '{model_name}' is not available on OpenRouter. Pick a model from the catalog."
            )
        return attrs


class EvaluationScopeSerializer(serializers.Serializer):
    project = serializers.PrimaryKeyRelatedField(queryset=Project.objects.all())
    dataset = serializers.PrimaryKeyRelatedField(
        queryset=Dataset.objects.select_related("capability")
    )
    cell = serializers.PrimaryKeyRelatedField(
        queryset=Cell.objects.all(), required=False, allow_null=True
    )
    eval_set = serializers.PrimaryKeyRelatedField(
        queryset=EvalSet.objects.all(), required=False, allow_null=True
    )
    judge_model = serializers.ChoiceField(
        choices=["", *judge_picker_models()],
        required=False,
        default="",
        help_text="Explanation judge for this run. Blank uses the configured default.",
    )
    targets = EvalRunTargetSerializer(many=True, max_length=MAX_TARGETS)

    def validate_project(self, value):
        return require_membership(self, value)

    def validate(self, attrs):
        project = attrs["project"]
        require_same_project(project, dataset=attrs["dataset"], eval_set=attrs.get("eval_set"))
        cell = attrs.get("cell")
        if cell is not None and cell.dataset_id != attrs["dataset"].pk:
            raise serializers.ValidationError(
                {"cell": "This version belongs to a different dataset."}
            )
        for target in attrs.get("targets", []):
            require_same_project(project, targets=target.get("model_ref"))
        capability = attrs["dataset"].capability
        if attrs.get("eval_set") is None and capability is not None:
            attrs["eval_set"] = capability.active_eval_set
        return attrs

    def matrix(self) -> list[dict]:
        data = self.validated_data
        return runs.freeze_matrix(
            eval_set=data.get("eval_set"),
            capability=data["dataset"].capability,
            project=data["project"],
        )


class EvaluationContextRequestSerializer(EvaluationScopeSerializer):
    targets = EvalRunTargetSerializer(many=True, max_length=MAX_TARGETS, required=False)

    def report(self) -> dict:
        data = self.validated_data
        checks = context.check_context(
            dataset=data["dataset"],
            cell=data.get("cell") or data["dataset"].active_cell,
            targets=data.get("targets", []),
            has_llm_rows=any(row["type"] == EvalMatrix.Type.LLM for row in self.matrix()),
            judge_model=data.get("judge_model", ""),
        )
        return {"checks": checks, "judge_models": context.judge_model_options(checks)}


class EvalRunCreateSerializer(EvaluationScopeSerializer):
    name = serializers.CharField(max_length=255)
    description = serializers.CharField(required=False, allow_blank=True, default="")
    max_items = serializers.IntegerField(required=False, min_value=0, default=0)
    sampling = serializers.FloatField(required=False, min_value=0.0, max_value=1.0, default=1.0)

    def validate(self, attrs):
        attrs = super().validate(attrs)
        if not attrs["targets"]:
            raise serializers.ValidationError({"targets": "Add at least one target."})
        if sum(target["is_baseline"] for target in attrs["targets"]) > 1:
            raise serializers.ValidationError(
                {"targets": "Mark at most one target as the baseline."}
            )
        try:
            attrs["cell"] = use.check(attrs["dataset"], "eval", cell=attrs.get("cell"))
        except DatasetError as exc:
            raise serializers.ValidationError({"dataset": exc.detail}) from exc
        if any(target["mode"] == "generate" for target in attrs["targets"]):
            from overbae.services.datasets.contract import evaluation_generation_error

            if reason := evaluation_generation_error(attrs["cell"].intent_report):
                raise serializers.ValidationError({"dataset": reason})
        return attrs

    def save(self, **kwargs) -> dict:
        """One run per target in one group; each is launched once committed."""
        data = self.validated_data
        matrix = self.matrix()
        if not matrix:
            raise serializers.ValidationError({"eval_set": "No enabled eval matrix rows apply."})
        group = uuid.uuid4()
        created = []
        with transaction.atomic():
            use.freeze(data["cell"])
            for target in data["targets"]:
                ref = target.get("model_ref")
                label = target.get("label") or (
                    ref.label if ref else target.get("model_name") or target["mode"]
                )
                run = runs.create_run(
                    project=data["project"],
                    name=data["name"]
                    if len(data["targets"]) == 1
                    else f"{data['name']} · {label}"[:255],
                    description=data["description"],
                    dataset=data["dataset"],
                    cell=data["cell"],
                    eval_set=data.get("eval_set"),
                    matrix=matrix,
                    mode=target["mode"],
                    model_ref=ref,
                    model_name=target.get("model_name", ""),
                    params={**target.get("params", {}), "label": label},
                    group=group,
                    is_baseline=target["is_baseline"],
                    judge_model=data["judge_model"],
                    max_items=data["max_items"],
                    sampling=data["sampling"],
                    **kwargs,
                )
                runs.launch(run)
                created.append(run)
        return {"group": group, "runs": created}


class EvalRunProgressSerializer(serializers.Serializer):
    rows = serializers.IntegerField()
    waiting = serializers.IntegerField()
    running = serializers.IntegerField()
    done = serializers.IntegerField()
    errored = serializers.IntegerField()
    phase = serializers.ChoiceField(choices=["preparing", "generating", "scoring"])


def run_progress(run: EvalRun) -> dict:
    counts = dict.fromkeys(EvalRunRow.State.values, 0)
    errored = 0
    for state, error in run.rows.values_list("state", "error"):
        counts[state] += 1
        errored += bool(error)
    rows = sum(counts.values())
    unfinished = sum(counts[state] for state in generation.UNFINISHED)
    phase = (
        "preparing"
        if not rows
        else "scoring"
        if run.scoring_task_id or not unfinished
        else "generating"
    )
    return {
        "rows": rows,
        "waiting": counts[EvalRunRow.State.WAITING] + counts[EvalRunRow.State.QUEUED],
        "running": counts[EvalRunRow.State.RUNNING],
        "done": rows - unfinished,
        "errored": errored,
        "phase": phase,
    }


class EvalRunSerializer(serializers.ModelSerializer):
    label = serializers.CharField(read_only=True)
    resolved_model = serializers.CharField(read_only=True)
    dataset_name = serializers.CharField(source="dataset.name", read_only=True, default="")
    capability_id = serializers.UUIDField(
        source="dataset.capability_id", read_only=True, default=None
    )
    capability_name = serializers.CharField(
        source="dataset.capability.name", read_only=True, default=""
    )
    cell_info = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()
    origin = serializers.CharField(read_only=True, default="manual")
    optimizer_experiment = serializers.UUIDField(read_only=True, default=None, allow_null=True)

    class Meta:
        model = EvalRun
        fields = [
            "id",
            "project",
            "name",
            "description",
            "label",
            "dataset",
            "dataset_name",
            "capability_id",
            "capability_name",
            "cell",
            "cell_info",
            "max_items",
            "sampling",
            "eval_set",
            "matrix",
            "mode",
            "model_ref",
            "model_name",
            "resolved_model",
            "params",
            "group",
            "is_baseline",
            "judge_model",
            "status",
            "error",
            "summary",
            "progress",
            "origin",
            "optimizer_experiment",
            "triggered_by",
            "created_at",
            "updated_at",
            "completed_at",
        ]
        read_only_fields = fields

    def get_cell_info(self, obj) -> dict | None:
        return use.describe(obj.cell if obj.cell_id else None)

    @extend_schema_field(EvalRunProgressSerializer(allow_null=True))
    def get_progress(self, obj):
        return None if obj.is_terminal else run_progress(obj)


class EvalRunDetailSerializer(EvalRunSerializer):
    context_checks = serializers.SerializerMethodField()

    class Meta(EvalRunSerializer.Meta):
        fields = [*EvalRunSerializer.Meta.fields, "context_checks"]
        read_only_fields = fields

    @extend_schema_field(EvaluationContextCheckSerializer(many=True))
    def get_context_checks(self, obj):
        return context.run_context_checks(obj)


class EvalRunGroupSerializer(serializers.Serializer):
    group = serializers.UUIDField()
    runs = EvalRunSerializer(many=True)


class EvalScoreSerializer(serializers.ModelSerializer):
    class Meta:
        model = EvalScore
        fields = [
            "id",
            "project",
            "source_type",
            "source_id",
            "run",
            "score",
            "metrics",
            "created_at",
        ]
        read_only_fields = fields


class EvalRunRowSerializer(serializers.ModelSerializer):
    """``score`` and ``metrics`` are the row's newest score, passed in by the view."""

    score = serializers.SerializerMethodField()
    metrics = serializers.SerializerMethodField()
    input = serializers.SerializerMethodField()
    reference = serializers.SerializerMethodField()

    class Meta:
        model = EvalRunRow
        fields = [
            "id",
            "run",
            "row_index",
            "source_trace_id",
            "input",
            "output",
            "reference",
            "metadata",
            "error",
            "degraded",
            "degraded_reason",
            "state",
            "score",
            "metrics",
            "finished_at",
        ]
        read_only_fields = fields

    def latest(self, obj) -> EvalScore | None:
        return self.context.get("scores", {}).get(f"{obj.run.cell_id}:{obj.row_index}")

    def get_score(self, obj) -> float | None:
        found = self.latest(obj)
        return found.score if found else None

    @extend_schema_field(serializers.DictField())
    def get_metrics(self, obj) -> dict:
        found = self.latest(obj)
        return found.metrics if found else {}

    @staticmethod
    def source_index(obj):
        legacy = obj.metadata.get("legacy_sample", {})
        return legacy.get("row_index", obj.row_index)

    def dataset_row(self, obj):
        index = self.source_index(obj)
        if not obj.run.cell_id or index is None:
            return None
        if "dataset_rows" in self.context:
            return self.context["dataset_rows"].get(index)
        cache = self.context.setdefault("dataset_row_cache", {})
        key = (obj.run.cell_id, index)
        if key not in cache:
            found = row_store.row(obj.run.cell, index)
            cache[key] = generation.prepared(found) if found else None
        return cache[key]

    @extend_schema_field(serializers.JSONField(allow_null=True))
    def get_input(self, obj):
        row = self.dataset_row(obj)
        return row.input if row else None

    @extend_schema_field(serializers.JSONField(allow_null=True))
    def get_reference(self, obj):
        row = self.dataset_row(obj)
        return generation.reference_of(row, obj.run.mode) if row else None


class EvalRunRowDetailSerializer(EvalRunRowSerializer):
    """Keep the detail response type distinct for existing API clients."""


class MetricComparisonSerializer(serializers.Serializer):
    name = serializers.CharField()
    baseline = serializers.FloatField(allow_null=True)
    candidate = serializers.FloatField(allow_null=True)
    delta = serializers.FloatField(allow_null=True)
    status = serializers.CharField()


class OverallComparisonSerializer(serializers.Serializer):
    baseline = serializers.FloatField(allow_null=True)
    candidate = serializers.FloatField(allow_null=True)
    delta = serializers.FloatField(allow_null=True)
    status = serializers.CharField()


class EvalRunComparisonSerializer(serializers.Serializer):
    baseline_run_id = serializers.UUIDField()
    candidate_run_id = serializers.UUIDField()
    baseline_label = serializers.CharField()
    candidate_label = serializers.CharField()
    overall = OverallComparisonSerializer()
    paired = serializers.IntegerField()
    wins = serializers.IntegerField()
    losses = serializers.IntegerField()
    ties = serializers.IntegerField()
    metrics = MetricComparisonSerializer(many=True)
    trust = serializers.DictField()
