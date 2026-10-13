from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from overbae.services.datasets import review
from overbae.services.mcp.contracts.common import (
    JobReceipt,
    MCPModel,
    PageContract,
    ResourceLinkContract,
)
from overbae.services.mcp.contracts.datasets import NextAction


def compact_run(record: dict, evidence_resource: dict) -> dict:
    def measured(scope):
        result = {key: value for key, value in scope.items() if key != "sample"}
        if "impact" in result:
            result["impact"] = review.summary(result["impact"])
        if "sample" in scope:
            result["sample_count"] = len(scope["sample"])
        return result

    progress = measured(record.get("result") or {})
    if "steps" in progress:
        progress["steps"] = [measured(step) for step in progress["steps"]]
    return {**record, "result": progress, "evidence_resource": evidence_resource}


class DatasetInput(MCPModel):
    dataset: str = Field(min_length=1, max_length=255)


class InspectInput(MCPModel):
    dataset: str | None = Field(default=None, max_length=255)
    pipeline: UUID | None = None
    pipeline_offset: int = Field(default=0, ge=0)
    run_offset: int = Field(default=0, ge=0)
    binding_offset: int = Field(default=0, ge=0)
    limit: int = Field(default=20, ge=1, le=100)


class SaveInput(MCPModel):
    dataset: str | None = Field(default=None, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    request_key: str = Field(min_length=1, max_length=128)
    pipeline: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    derived_from: UUID | None = None
    package: UUID = Field(
        description="Required retained Python package UUID from overmind dataset pipeline-upload. "
        "Author meaningful stages in entrypoint files; read overmind://dataset-upload for the manifest."
    )

    @model_validator(mode="before")
    @classmethod
    def require_retained_code(cls, value):
        if isinstance(value, dict) and not value.get("package"):
            raise ValueError(
                "A retained Python package is required. Author meaningful stages, upload with "
                "overmind dataset pipeline-upload, then supply package. Read overmind://dataset-upload."
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


class ValidateInput(MCPModel):
    pipeline: UUID
    source_cell: UUID | None = None
    source_fingerprint: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


class CancelRunInput(MCPModel):
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


class BindingInput(MCPModel):
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
            for field in ("name", "intent")
        ):
            raise ValueError("Name and intent cannot be null.")
        return self


class Pipeline(MCPModel):
    id: str
    pipeline_id: str
    revision: int
    parent: str | None
    derived_from: str | None
    package: str | None
    runtime: str
    executable: bool
    name: str
    fingerprint: str
    steps: list[dict[str, Any]]
    flow: dict[str, Any]
    created_at: str


class Run(MCPModel):
    id: str
    pipeline: str | None
    pipeline_name: str | None
    revision: int | None
    source_dataset: str
    source_cell: str
    source_fingerprint: str
    artifact_cell: str | None
    artifact_fingerprint: str
    provenance: str
    execution: Literal["platform", "external_attributed", "isolated_container"]
    mode: str
    parameters: dict[str, Any]
    binding: str | None
    operation: dict[str, Any] | None
    runner: dict[str, Any] | None
    output_cell: str | None
    state: str
    error: str
    result: dict[str, Any]
    evidence_resource: ResourceLinkContract
    created_at: str
    updated_at: str
    completed_at: str | None
    poll_after_seconds: int | None
    queue_seconds: float | None


class Output(MCPModel):
    summary: str
    project_id: str
    next_actions: list[NextAction] = Field(default_factory=list, max_length=8)
    dataset: str | None = None
    preparation: dict[str, Any] | None = None
    current_pipeline: str | None = None
    pipeline: Pipeline | None = None
    run: Run | None = None
    job: JobReceipt | None = None
    pipelines: list[Pipeline] = Field(default_factory=list)
    runs: list[Run] = Field(default_factory=list)
    pipeline_page: PageContract | None = None
    run_page: PageContract | None = None
    binding_page: PageContract | None = None
    binding: dict[str, Any] | None = None
    bindings: list[dict[str, Any]] = Field(default_factory=list)
    validation: dict[str, Any] | None = None
    runner: dict[str, Any] | None = None
    authoring: dict[str, Any] | None = None
    semantic_quality: str = "unmeasured"
    resource_links: list[ResourceLinkContract]
