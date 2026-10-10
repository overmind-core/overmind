from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from overbae.models import Cell, Dataset
from overbae.services.datasets import lifecycle, review, transformation
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.land import SPLIT_POSITIONS


class DatasetReadinessSerializer(serializers.Serializer):
    assessment = serializers.DictField(
        child=serializers.ChoiceField(choices=["pass", "fail", "partial", "unknown"])
    )
    format_valid = serializers.BooleanField()
    format_reason = serializers.CharField(allow_blank=True)
    quality_reviewed = serializers.BooleanField()
    quality_passed = serializers.BooleanField()
    quality_reason = serializers.CharField(allow_blank=True)
    training_configuration = serializers.CharField()


class CellTransformationSerializer(serializers.Serializer):
    execution = serializers.ChoiceField(
        choices=[
            "source",
            "unrecorded",
            "isolated_container",
            "platform_operations",
            "external_import",
        ]
    )
    run = serializers.UUIDField(allow_null=True)
    pipeline = serializers.UUIDField(allow_null=True)
    revision = serializers.IntegerField(allow_null=True)
    package = serializers.UUIDField(allow_null=True)
    package_sha256 = serializers.CharField(allow_blank=True)
    entrypoint = serializers.CharField(allow_blank=True)
    provenance = serializers.CharField(allow_blank=True)


class CellSerializer(serializers.ModelSerializer):
    """A cell with its derived version. The view passes ``versions`` and
    ``frozen_before`` in the context so a chain costs no extra queries."""

    version = serializers.SerializerMethodField()
    frozen = serializers.SerializerMethodField()
    fits = serializers.SerializerMethodField()
    readiness = serializers.SerializerMethodField()
    preparation_plan = serializers.SerializerMethodField()
    usage = serializers.SerializerMethodField()
    transformation = serializers.SerializerMethodField()

    class Meta:
        model = Cell
        fields = [
            "id",
            "position",
            "version",
            "title",
            "script",
            "transformation",
            "note",
            "state",
            "error",
            "frozen",
            "rows",
            "columns",
            "fingerprint",
            "input_fingerprint",
            "intent_report",
            "capability_report",
            "fits",
            "stats",
            "review",
            "quality_report",
            "preparation_plan",
            "readiness",
            "seconds",
            "used_at",
            "usage",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_version(self, obj) -> str:
        versions = self.context.get("versions")
        if versions is None:
            versions = obj.dataset.versions()
        return versions.get(obj.id, "")

    @extend_schema_field(CellTransformationSerializer)
    def get_transformation(self, obj):
        records = self.context.get("transformations")
        if records is None:
            records = transformation.records(obj.dataset, [obj])
        return CellTransformationSerializer(records[obj.pk]).data

    @extend_schema_field(
        serializers.DictField(child=serializers.ListField(child=serializers.DictField()))
    )
    def get_usage(self, obj):
        return lifecycle.usage(obj) if obj.used_at else {}

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_preparation_plan(self, obj):
        return obj.preparation_plan or None

    def get_frozen(self, obj) -> bool:
        frozen_before = self.context.get("frozen_before")
        if frozen_before is None:
            return obj.frozen
        return obj.used_at is not None or obj.position <= frozen_before

    def get_fits(self, obj) -> dict:
        intent = self.context.get("intent") or obj.dataset.intent
        ok, reason = obj.fits(intent)
        return {"ok": ok, "reason": reason}

    @extend_schema_field(DatasetReadinessSerializer)
    def get_readiness(self, obj):
        return review.readiness(
            self.context.get("dataset") or obj.dataset,
            obj,
            context=self.context.get("preparation_context"),
        )


class DatasetSerializer(serializers.ModelSerializer):
    preparation_plan = serializers.SerializerMethodField()
    capability_name = serializers.CharField(source="capability.name", read_only=True, default=None)
    cells = serializers.SerializerMethodField()
    active_version = serializers.SerializerMethodField()
    rows = serializers.SerializerMethodField()
    readiness = serializers.SerializerMethodField()

    class Meta:
        model = Dataset
        fields = [
            "id",
            "project",
            "name",
            "brief",
            "source_kind",
            "source_spec",
            "capability",
            "capability_name",
            "capability_rank",
            "intent",
            "active",
            "active_version",
            "rows",
            "readiness",
            "preparation_plan",
            "operation",
            "state",
            "error",
            "cells",
            "created_by",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            f for f in fields if f not in ("name", "capability", "intent", "active")
        ]

    def _chain(self, obj) -> list[Cell]:
        cached = getattr(obj, "_prefetched_objects_cache", {}).get("cells")
        return sorted(cached, key=lambda c: c.position) if cached is not None else obj.chain

    @extend_schema_field(serializers.DictField(allow_null=True))
    def get_preparation_plan(self, obj):
        return obj.preparation_plan or None

    def _context_fingerprint(self, obj):
        fingerprints = self.context.setdefault("capability_fingerprints", {})
        if obj.capability_id not in fingerprints:
            fingerprints[obj.capability_id] = context_fingerprint(obj.capability)
        return fingerprints[obj.capability_id]

    @extend_schema_field(DatasetReadinessSerializer(allow_null=True))
    def get_readiness(self, obj):
        active = self._active(obj)
        return (
            review.readiness(obj, active, context=self._context_fingerprint(obj))
            if active
            else None
        )

    @extend_schema_field(CellSerializer(many=True))
    def get_cells(self, obj) -> list[dict]:
        if self.context.get("summary"):
            return []
        chain = self._chain(obj)
        versions = obj.versions(chain=chain)
        frozen = max((c.position for c in chain if c.used_at is not None), default=-1)
        return CellSerializer(
            chain,
            many=True,
            context={
                "versions": versions,
                "frozen_before": frozen,
                "intent": obj.intent,
                "dataset": obj,
                "preparation_context": self._context_fingerprint(obj),
                "transformations": transformation.records(obj, chain),
            },
        ).data

    def _active(self, obj) -> Cell | None:
        chain = self._chain(obj)
        ran = [c for c in chain if c.state == Cell.State.OK and c.fingerprint]
        if obj.active_id is not None:
            for cell in ran:
                if cell.id == obj.active_id:
                    return cell
        return ran[-1] if ran else None

    def get_active_version(self, obj) -> str:
        cell = self._active(obj)
        return obj.versions(chain=self._chain(obj)).get(cell.id, "") if cell else ""

    def get_rows(self, obj) -> int:
        cell = self._active(obj)
        return int(cell.rows) if cell else 0

    def validate_capability(self, value):
        if value is not None and value.project_id != self.instance.project_id:
            raise serializers.ValidationError("That capability belongs to another project.")
        return value

    def validate_active(self, value):
        if value is not None and value.dataset_id != self.instance.id:
            raise serializers.ValidationError("That version belongs to another dataset.")
        return value


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
    name = serializers.CharField(max_length=255, required=False, default="Untitled dataset")
    brief = serializers.CharField(max_length=8000, required=False, allow_blank=True, default="")
    project = serializers.UUIDField()
    capability = serializers.UUIDField(
        required=False, allow_null=True, help_text="Omit to infer from the rows; null means none."
    )
    intent = serializers.ChoiceField(choices=Dataset.Intent.choices, required=False)
    source = SourceSerializer(required=False)

    def validate(self, attrs):
        if not attrs.get("source") and not attrs.get("brief"):
            raise serializers.ValidationError("Describe what you want to do or add source data.")
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
    brief = serializers.CharField(max_length=8000, required=False, allow_blank=True, default="")
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
    approximate = serializers.BooleanField()
    sample_rows = serializers.IntegerField()
    total_rows = serializers.IntegerField()
    method = serializers.CharField()
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


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()
