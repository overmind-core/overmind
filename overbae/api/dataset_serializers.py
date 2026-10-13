from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from overbae.models import Cell, Dataset, Execution
from overbae.services.datasets.imports import resumable_imports
from overbae.services.datasets.land import SPLIT_POSITIONS


class CellSerializer(serializers.ModelSerializer):
    version = serializers.CharField(source="label", read_only=True)
    frozen = serializers.BooleanField(read_only=True)
    fits = serializers.SerializerMethodField()

    class Meta:
        model = Cell
        fields = [
            "id",
            "version",
            "position",
            "parent",
            "title",
            "script",
            "rows",
            "columns",
            "fingerprint",
            "intent_report",
            "capability_report",
            "fits",
            "stats",
            "impact",
            "stdout",
            "seconds",
            "used_at",
            "frozen",
            "created_at",
        ]
        read_only_fields = fields

    def get_fits(self, obj) -> dict:
        intent = self.context.get("intent") or obj.dataset.intent
        ok, reason = obj.fits(intent)
        return {"ok": ok, "reason": reason}


class DatasetSerializer(serializers.ModelSerializer):
    capability_name = serializers.CharField(source="capability.name", read_only=True, default=None)
    cells = serializers.SerializerMethodField()
    active_version = serializers.SerializerMethodField()
    rows = serializers.SerializerMethodField()
    running = serializers.SerializerMethodField()
    import_blocked = serializers.SerializerMethodField()
    decision_ready = serializers.SerializerMethodField(
        help_text="The active version is valid decision rows, for decision models."
    )
    expected_active = serializers.PrimaryKeyRelatedField(
        queryset=Cell.objects.all(),
        write_only=True,
        required=False,
        help_text="With active: refuse with 409 active_conflict unless this is the active version.",
    )

    class Meta:
        model = Dataset
        fields = [
            "id",
            "project",
            "name",
            "source_kind",
            "source_spec",
            "capability",
            "capability_name",
            "capability_rank",
            "intent",
            "active",
            "expected_active",
            "active_version",
            "rows",
            "state",
            "error",
            "running",
            "import_blocked",
            "decision_ready",
            "cells",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            f
            for f in fields
            if f not in ("name", "capability", "intent", "active", "expected_active")
        ]

    def _versions(self, obj) -> list[Cell]:
        cached = getattr(obj, "_prefetched_objects_cache", {}).get("cells")
        return sorted(cached, key=lambda c: c.position) if cached is not None else obj.versions

    def _active(self, obj) -> Cell | None:
        return next((c for c in self._versions(obj) if c.id == obj.active_id), None)

    @extend_schema_field(CellSerializer(many=True))
    def get_cells(self, obj) -> list[dict]:
        if self.context.get("summary"):
            return []
        return CellSerializer(self._versions(obj), many=True, context={"intent": obj.intent}).data

    def get_decision_ready(self, obj) -> bool:
        active = self._active(obj)
        return bool(active is not None and active.fits_decision()[0])

    def get_running(self, obj) -> int:
        if hasattr(obj, "running_count"):
            return obj.running_count
        return obj.executions.filter(
            state__in=[Execution.State.QUEUED, Execution.State.RUNNING]
        ).count()

    def get_import_blocked(self, obj) -> bool:
        if hasattr(obj, "import_blocked"):
            return obj.import_blocked
        return resumable_imports(obj.pk).exists()

    def get_active_version(self, obj) -> str:
        cell = self._active(obj)
        return cell.label if cell else ""

    def get_rows(self, obj) -> int:
        cell = self._active(obj)
        return int(cell.rows) if cell else 0

    def validate_capability(self, value):
        if value is not None and value.project_id != self.instance.project_id:
            raise serializers.ValidationError("That capability belongs to another project.")
        return value

    def validate_active(self, value):
        if value is None or value.dataset_id != self.instance.id:
            raise serializers.ValidationError("That version belongs to another dataset.")
        return value

    def validate_expected_active(self, value):
        if value.dataset_id != self.instance.id:
            raise serializers.ValidationError("That version belongs to another dataset.")
        return value


class ExecutionSerializer(serializers.ModelSerializer):
    error = serializers.SerializerMethodField()
    script_head = serializers.SerializerMethodField()
    parent = serializers.SerializerMethodField()
    cell = serializers.SerializerMethodField()
    created_by = serializers.SerializerMethodField()

    class Meta:
        model = Execution
        fields = [
            "id",
            "mode",
            "title",
            "state",
            "transient",
            "error",
            "script_head",
            "parent",
            "cell",
            "queued_at",
            "started_at",
            "finished_at",
            "run_seconds",
            "created_by",
        ]
        read_only_fields = fields

    def get_error(self, obj) -> str:
        return obj.error[:300]

    def get_script_head(self, obj) -> str:
        if obj.mode == Execution.Mode.SYNTHETIC:
            return ""
        return next((line for line in obj.script.splitlines() if line.strip()), "")[:120]

    def get_parent(self, obj) -> str | None:
        return obj.parent.label if obj.parent_id else None

    def get_cell(self, obj) -> str | None:
        return obj.cell.label if obj.cell_id else None

    def get_created_by(self, obj) -> str | None:
        user = obj.created_by
        if user is None:
            return None
        return user.get_full_name() or user.email


class SourceSerializer(serializers.Serializer):
    """Exactly one of ``uploads``, ``upload_id``, ``text``, ``rows``, ``traces`` or ``llm_calls``.
    ``traces`` is a traces-list selection or ``{"trace_ids": [...]}``.
    ``llm_calls`` is ``{capability_id, since, until?, model?, limit?}``."""

    upload_id = serializers.UUIDField(required=False, allow_null=True)
    uploads = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=False, max_length=100
    )
    filename = serializers.CharField(required=False, allow_blank=True)
    text = serializers.CharField(required=False, allow_blank=True)
    rows = serializers.ListField(child=serializers.JSONField(), required=False)
    traces = serializers.JSONField(required=False)
    llm_calls = serializers.JSONField(required=False)
    json_rows_field = serializers.CharField(
        max_length=200,
        required=False,
        help_text="Top-level JSON array to import from a .json upload.",
    )

    def validate(self, attrs):
        keys = [
            k
            for k in ("uploads", "upload_id", "text", "rows", "traces", "llm_calls")
            if attrs.get(k)
        ]
        if len(keys) != 1:
            raise serializers.ValidationError(
                "Give exactly one source: uploads, upload_id, text, rows, traces or llm_calls."
            )
        if attrs.get("upload_id"):
            attrs["upload_id"] = str(attrs["upload_id"])
        if "uploads" in attrs:
            attrs["uploads"] = [str(upload_id) for upload_id in attrs["uploads"]]
            if len(set(attrs["uploads"])) != len(attrs["uploads"]):
                raise serializers.ValidationError("Each upload may only be included once.")
        return attrs


class DatasetCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    project = serializers.UUIDField()
    capability = serializers.UUIDField(
        required=False, allow_null=True, help_text="Omit to infer from the rows; null means none."
    )
    intent = serializers.ChoiceField(choices=Dataset.Intent.choices, required=False)
    source = SourceSerializer()

    def validate(self, attrs):
        if (attrs.get("source") or {}).get("llm_calls") and attrs.get("intent") not in (
            Dataset.Intent.TRAIN,
            Dataset.Intent.EVAL,
        ):
            raise serializers.ValidationError({"intent": "Choose train or eval."})
        return attrs


class DatasetSplitCreateSerializer(serializers.Serializer):
    """One source landed as ``<name> train`` and ``<name> eval``. The eval slice is
    ``eval_percent`` of the rows taken at ``position``."""

    name = serializers.CharField(max_length=249)
    project = serializers.UUIDField()
    capability = serializers.UUIDField(
        required=False, allow_null=True, help_text="Omit to infer from the rows; null means none."
    )
    source = SourceSerializer()
    eval_percent = serializers.IntegerField(min_value=1, max_value=99)
    position = serializers.ChoiceField(choices=[*SPLIT_POSITIONS, "hash"])
    group_by = serializers.ListField(
        child=serializers.CharField(max_length=255), max_length=10, required=False, default=list
    )
    stratify_by = serializers.CharField(
        max_length=255, required=False, allow_null=True, default=None
    )
    deduplicate = serializers.BooleanField(default=True)


class DatasetPairSerializer(serializers.Serializer):
    train = DatasetSerializer()
    eval = DatasetSerializer()


class RowsPageSerializer(serializers.Serializer):
    rows = serializers.ListField(child=serializers.JSONField())
    total = serializers.IntegerField()
    columns = serializers.ListField(child=serializers.JSONField())
    offset = serializers.IntegerField()
    limit = serializers.IntegerField()
    marks = serializers.JSONField(required=False)


class ColumnStatSerializer(serializers.Serializer):
    name = serializers.CharField()
    type = serializers.CharField()
    null_rate = serializers.FloatField()
    distinct = serializers.IntegerField()
    min = serializers.JSONField(required=False, allow_null=True)
    max = serializers.JSONField(required=False, allow_null=True)
    mean = serializers.FloatField(required=False, allow_null=True)
    mean_len = serializers.FloatField(required=False, allow_null=True)
    max_len = serializers.IntegerField(required=False, allow_null=True)
    top = serializers.ListField(child=serializers.JSONField(), required=False)
