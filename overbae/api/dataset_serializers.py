from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from overbae.models import Cell, Dataset
from overbae.services.datasets import review
from overbae.services.datasets.context import context_fingerprint
from overbae.services.datasets.land import SPLIT_POSITIONS


class DatasetReadinessSerializer(serializers.Serializer):
    format_valid = serializers.BooleanField()
    format_reason = serializers.CharField(allow_blank=True)
    quality_reviewed = serializers.BooleanField()
    quality_passed = serializers.BooleanField()
    quality_reason = serializers.CharField(allow_blank=True)
    training_configuration = serializers.CharField()


class CellSerializer(serializers.ModelSerializer):
    """A cell with its derived version. The view passes ``versions`` and
    ``frozen_before`` in the context so a chain costs no extra queries."""

    version = serializers.SerializerMethodField()
    frozen = serializers.SerializerMethodField()
    fits = serializers.SerializerMethodField()
    readiness = serializers.SerializerMethodField()

    class Meta:
        model = Cell
        fields = [
            "id",
            "position",
            "version",
            "title",
            "script",
            "note",
            "state",
            "error",
            "frozen",
            "rows",
            "columns",
            "fingerprint",
            "intent_report",
            "capability_report",
            "fits",
            "stats",
            "review",
            "quality_report",
            "readiness",
            "seconds",
            "used_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields

    def get_version(self, obj) -> str:
        versions = self.context.get("versions")
        if versions is None:
            versions = obj.dataset.versions()
        return versions.get(obj.id, "")

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


class ChatTurnSerializer(serializers.Serializer):
    id = serializers.CharField(required=False)
    role = serializers.ChoiceField(choices=["user", "agent"])
    text = serializers.CharField(allow_blank=True)
    error = serializers.CharField(required=False, allow_blank=True)
    cells = serializers.ListField(child=serializers.JSONField(), required=False)
    steps = serializers.ListField(child=serializers.JSONField(), required=False)
    ms = serializers.IntegerField(required=False)
    status = serializers.ChoiceField(
        choices=["running", "awaiting_approval", "resolved", "complete", "error"], required=False
    )
    progress = serializers.JSONField(required=False)
    at = serializers.CharField()


class DatasetSerializer(serializers.ModelSerializer):
    capability_name = serializers.CharField(source="capability.name", read_only=True, default=None)
    cells = serializers.SerializerMethodField()
    chat = ChatTurnSerializer(many=True, read_only=True)
    active_version = serializers.SerializerMethodField()
    rows = serializers.SerializerMethodField()
    readiness = serializers.SerializerMethodField()

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
            "active_version",
            "rows",
            "readiness",
            "state",
            "error",
            "cells",
            "chat",
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
    """Exactly one of ``uploads``, ``upload_id``, ``text``, ``rows`` or ``traces``.
    ``traces`` is a traces-list selection or ``{"trace_ids": [...]}``."""

    upload_id = serializers.UUIDField(required=False, allow_null=True)
    uploads = serializers.ListField(
        child=serializers.UUIDField(), required=False, allow_empty=False, max_length=100
    )
    filename = serializers.CharField(required=False, allow_blank=True)
    text = serializers.CharField(required=False, allow_blank=True)
    rows = serializers.ListField(child=serializers.JSONField(), required=False)
    traces = serializers.JSONField(required=False)

    def validate(self, attrs):
        keys = [k for k in ("uploads", "upload_id", "text", "rows", "traces") if attrs.get(k)]
        if len(keys) != 1:
            raise serializers.ValidationError(
                "Give exactly one source: uploads, upload_id, text, rows or traces."
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
    position = serializers.ChoiceField(choices=SPLIT_POSITIONS)
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


class CellWriteSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, max_length=255)
    script = serializers.CharField(required=False, allow_blank=True, trim_whitespace=False)
    note = serializers.CharField(required=False, allow_blank=True, max_length=512)


class CellCreateSerializer(CellWriteSerializer):
    title = serializers.CharField(max_length=255)
    script = serializers.CharField(allow_blank=True, trim_whitespace=False)


class ChatSerializer(serializers.Serializer):
    message = serializers.CharField(max_length=8000)


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


class DetailSerializer(serializers.Serializer):
    detail = serializers.CharField()
