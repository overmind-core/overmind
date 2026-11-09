from typing import Any, Literal

from pydantic import Field

from overbae.services.datasets import review
from overbae.services.datasets.workbench_requests import (
    BindingInput as BindingInput,
)
from overbae.services.datasets.workbench_requests import (
    BindingStateInput as BindingStateInput,
)
from overbae.services.datasets.workbench_requests import (
    CancelRunInput as CancelRunInput,
)
from overbae.services.datasets.workbench_requests import (
    DatasetInput as DatasetInput,
)
from overbae.services.datasets.workbench_requests import (
    ImportInput as ImportInput,
)
from overbae.services.datasets.workbench_requests import (
    InspectInput as InspectInput,
)
from overbae.services.datasets.workbench_requests import (
    PreparationWorkflow as PreparationWorkflow,
)
from overbae.services.datasets.workbench_requests import (
    RunInput as RunInput,
)
from overbae.services.datasets.workbench_requests import (
    SaveBindingInput as SaveBindingInput,
)
from overbae.services.datasets.workbench_requests import (
    SaveInput as SaveInput,
)
from overbae.services.datasets.workbench_requests import (
    SourceInput as SourceInput,
)
from overbae.services.datasets.workbench_requests import (
    StartPreparationInput as StartPreparationInput,
)
from overbae.services.datasets.workbench_requests import (
    UpdateInput as UpdateInput,
)
from overbae.services.datasets.workbench_requests import (
    UpdatePreparationInput as UpdatePreparationInput,
)
from overbae.services.datasets.workbench_requests import (
    ValidateInput as ValidateInput,
)
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
    preparation_workflow: PreparationWorkflow | None = None
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
