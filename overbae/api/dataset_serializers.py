from __future__ import annotations

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from overbae.models import Cell, Dataset
from overbae.services.datasets.land import SPLIT_POSITIONS


class CellSerializer(serializers.ModelSerializer):
    """A cell with its derived version. The view passes ``versions`` and
    ``frozen_before`` in the context so a chain costs no extra queries."""

    version = serializers.SerializerMethodField()
    frozen = serializers.SerializerMethodField()
    fits = serializers.SerializerMethodField()

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


class ChatTurnSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=["user", "agent"])
    text = serializers.CharField(allow_blank=True)
    error = serializers.CharField(required=False, allow_blank=True)
    cells = serializers.ListField(child=serializers.JSONField(), required=False)
    at = serializers.CharField()


class DatasetSerializer(serializers.ModelSerializer):
    capability_name = serializers.CharField(source="capability.name", read_only=True, default=None)
    cells = serializers.SerializerMethodField()
    chat = ChatTurnSerializer(many=True, read_only=True)
    active_version = serializers.SerializerMethodField()
    rows = serializers.SerializerMethodField()

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

    @extend_schema_field(CellSerializer(many=True))
    def get_cells(self, obj) -> list[dict]:
        if self.context.get("summary"):
            return []
        chain = self._chain(obj)
        versions = _versions(chain)
        frozen = max((c.position for c in chain if c.used_at is not None), default=-1)
        return CellSerializer(
            chain,
            many=True,
            context={"versions": versions, "frozen_before": frozen, "intent": obj.intent},
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
        return _versions(self._chain(obj)).get(cell.id, "") if cell else ""

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


def _versions(chain: list[Cell]) -> dict:
    out: dict = {}
    major, minor = 1, 0
    for cell in chain:
        if cell.state == Cell.State.PROPOSED:
            continue
        if cell.position == 0:
            out[cell.id] = "1.0"
            continue
        if cell.used_at is not None:
            major, minor = major + 1, 0
        else:
            minor += 1
        out[cell.id] = f"{major}.{minor}"
    return out


class SourceSerializer(serializers.Serializer):
    """Exactly one of ``upload_id``, ``text``, ``rows`` or ``traces``.
    ``traces`` is a traces-list selection or ``{"trace_ids": [...]}``."""

    upload_id = serializers.UUIDField(required=False, allow_null=True)
    filename = serializers.CharField(required=False, allow_blank=True)
    text = serializers.CharField(required=False, allow_blank=True)
    rows = serializers.ListField(child=serializers.JSONField(), required=False)
    traces = serializers.JSONField(required=False)

    def validate(self, attrs):
        keys = [k for k in ("upload_id", "text", "rows", "traces") if attrs.get(k)]
        if len(keys) != 1:
            raise serializers.ValidationError(
                "Give exactly one source: upload_id, text, rows or traces."
            )
        if attrs.get("upload_id"):
            attrs["upload_id"] = str(attrs["upload_id"])
        return attrs


class DatasetCreateSerializer(serializers.Serializer):
    name = serializers.CharField(max_length=255)
    project = serializers.UUIDField()
    capability = serializers.UUIDField(required=False, allow_null=True)
    intent = serializers.ChoiceField(choices=Dataset.Intent.choices, required=False)
    source = SourceSerializer()


class DatasetSplitCreateSerializer(serializers.Serializer):
    """One source landed as ``<name> train`` and ``<name> eval``. The eval slice is
    ``eval_percent`` of the rows taken at ``position``."""

    name = serializers.CharField(max_length=249)
    project = serializers.UUIDField()
    capability = serializers.UUIDField(required=False, allow_null=True)
    source = SourceSerializer()
    eval_percent = serializers.IntegerField(min_value=1, max_value=99)
    position = serializers.ChoiceField(choices=SPLIT_POSITIONS)


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
