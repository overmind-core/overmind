from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class DatasetInput(RequestModel):
    dataset: str = Field(min_length=1, max_length=255)


class StartPreparationInput(DatasetInput):
    request_key: str = Field(min_length=1, max_length=128)


class UpdatePreparationInput(DatasetInput):
    preparation_id: UUID
    state: Literal["preparing", "paused", "completed", "cancelled"]
    outputs: list[UUID] = Field(default_factory=list, max_length=100)


class PreparationWorkflow(RequestModel):
    id: str
    dataset_id: str
    state: Literal["preparing", "paused", "completed", "cancelled"]
    actor_kind: str
    actor_name: str
    outputs: list[str]
    updated_at: datetime


class InspectInput(RequestModel):
    dataset: str | None = Field(default=None, max_length=255)
    pipeline: UUID | None = None
    pipeline_offset: int = Field(default=0, ge=0)
    run_offset: int = Field(default=0, ge=0)
    binding_offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class SaveInput(RequestModel):
    dataset: str | None = Field(default=None, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    request_key: str = Field(min_length=1, max_length=128)
    pipeline: UUID | None = Field(
        default=None,
        description="Family pipeline_id from inspection, not the immutable revision id. Required when revising an existing recipe.",
    )
    expected_revision: int | None = Field(default=None, ge=1)
    derived_from: UUID | None = None
    package: UUID = Field(
        description="Retained Python package UUID. See the pipeline authoring contract."
    )

    @model_validator(mode="before")
    @classmethod
    def require_retained_code(cls, value):
        if isinstance(value, dict) and not value.get("package"):
            raise ValueError(
                "A retained Python package is required. Author meaningful stages, retain with "
                "the package service, then supply package. Read the pipeline authoring contract."
            )
        return value


class SourceInput(DatasetInput):
    source_cell: UUID
    source_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    request_key: str = Field(min_length=1, max_length=128)


class RunInput(SourceInput):
    pipeline: UUID
    mode: Literal["publish", "preview"] = "publish"
    preview_rows: int = Field(default=100, ge=1, le=1000)
    parameters: dict[str, Any] = Field(default_factory=dict)


class ValidateInput(RequestModel):
    pipeline: UUID
    source_cell: UUID | None = None
    source_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class CancelRunInput(RequestModel):
    run: UUID


class SaveBindingInput(DatasetInput):
    pipeline: UUID
    request_key: str = Field(min_length=1, max_length=128)
    source_dataset: UUID | None = None
    trace_source: dict[str, Any] | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    trigger: Literal["manual", "scheduled", "ingestion"] = "manual"
    interval_seconds: int = Field(default=60, ge=10, le=86400)
    max_runs: int = Field(default=1000, ge=1, le=100000)
    max_source_rows: int = Field(default=1000000, ge=1, le=10000000)
    binding: UUID | None = None
    expected_version: int | None = Field(default=None, ge=1)


class BindingInput(RequestModel):
    binding: UUID


class BindingStateInput(BindingInput):
    expected_version: int = Field(ge=1)
    enabled: bool


class ImportInput(SourceInput):
    imported_rows: list[dict[str, Any]] | None = Field(default=None, min_length=1, max_length=2000)
    artifact_cell: UUID | None = None
    artifact_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=255)
    provenance: str = Field(min_length=1, max_length=8000)


class UpdateInput(DatasetInput):
    ocr_languages: list[str] | None = Field(
        default=None,
        max_length=4,
        description="Future-import Tesseract codes (e.g. [eng, fra]); [] English. Set before upload. Published cells unchanged.",
    )
    name: str | None = Field(default=None, min_length=1, max_length=255)
    intent: Literal["train", "eval", "explore"] | None = None
    active: UUID | None = None
    capability: UUID | None = None

    @model_validator(mode="after")
    def validate_changes(self):
        if not self.model_fields_set - {"dataset", "project_id"}:
            raise ValueError("Supply a dataset change.")
        if any(
            field in self.model_fields_set and getattr(self, field) is None
            for field in ("name", "intent", "ocr_languages")
        ):
            raise ValueError("Name, intent and OCR languages cannot be null.")
        return self
