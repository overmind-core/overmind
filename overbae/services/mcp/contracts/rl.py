from typing import Any, Literal

from pydantic import Field

from overbae.services.mcp.contracts.common import MCPModel


class RewardSpec(MCPModel):
    check: str = Field(
        min_length=1, max_length=64, description="From list_rl_training_options."
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Fixed parameter values.",
    )
    bind: dict[str, str] = Field(
        default_factory=dict,
        description="Parameter → column, read per row.",
    )
    attributes: dict[str, str] = Field(
        default_factory=dict,
        description="Span attribute → column (tool_selection reads overmind.reference_trajectory).",
    )
    expected: str = Field(
        "", max_length=255, description="Column read as span.expected."
    )
    weight: float = Field(1.0, gt=0, le=10)


class ListRLOptionsInput(MCPModel):
    project_id: str | None = None
    dataset: str | None = Field(
        None,
        description="Dataset UUID; when set, the output adds its active version's columns and first row.",
    )


class RLDatasetColumns(MCPModel):
    dataset: str
    cell: str
    version: str
    rows: int
    columns: list[str]
    first_row: dict[str, Any]


class ListRLOptionsOutput(MCPModel):
    summary: str
    models: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    hyperparameters: dict[str, dict[str, Any]]
    dataset: RLDatasetColumns | None = None
    guidance: list[str]


class PreviewRLRewardsInput(MCPModel):
    project_id: str | None = None
    dataset: str = Field(description="Dataset UUID.")
    rewards: list[RewardSpec] = Field(min_length=1, max_length=6)
    completions: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Candidate outputs scored against rows 0..n-1. Omit to score reference_column instead.",
    )
    reference_column: str = Field(
        "", max_length=255, description="Column holding a known-good output for each row."
    )
    rows: int = Field(5, ge=1, le=10)


class RewardPreviewRow(MCPModel):
    row: int
    output: str
    reward: float
    passed: bool
    checks: dict[str, dict[str, Any]]


class PreviewRLRewardsOutput(MCPModel):
    summary: str
    results: list[RewardPreviewRow]


class StartRLTrainingInput(MCPModel):
    project_id: str | None = None
    dataset: str = Field(
        description="Dataset UUID whose rows hold the prompts and per-row references."
    )
    cell: str = Field(
        "", max_length=64, description="Version label or cell UUID; blank uses the active version."
    )
    capability: str | None = Field(None, description="Capability UUID this run improves, if any.")
    name: str = Field("", max_length=255)
    base_model: str = Field(min_length=1, max_length=255)
    prompt_column: str = Field(
        min_length=1,
        max_length=255,
        description="Column with the prompt: a string, a list of chat messages, or {messages: [...]}. Trailing assistant turns are dropped.",
    )
    tools_column: str = Field(
        "",
        max_length=255,
        description="Column with OpenAI-style tool schemas rendered into the prompt.",
    )
    system_prompt: str = Field("", max_length=20000)
    rewards: list[RewardSpec] = Field(min_length=1, max_length=6)
    hyperparameters: dict[str, Any] = Field(default_factory=dict)
    request_key: str = Field(min_length=1, max_length=128)


class RLRunReceipt(MCPModel):
    id: str
    status: str
    name: str
    base_model: str
    train_rows: int
    holdout_rows: int
    max_steps: int
    console_url: str


class StartRLTrainingOutput(MCPModel):
    summary: str
    run: RLRunReceipt
    next_steps: list[str]


class InspectRLTrainingInput(MCPModel):
    project_id: str | None = None
    run: str = Field(description="RL run UUID.")
    samples: int = Field(
        3, ge=0, le=10, description="Held-out comparisons to return from each end of the change."
    )


class InspectRLTrainingOutput(MCPModel):
    summary: str
    run: dict[str, Any]
    progress: dict[str, Any]
    curve: list[dict[str, Any]]
    latest_group: dict[str, Any] | None = None
    comparison: dict[str, Any] | None = None
    verdict: dict[str, Any] | None = None
    console_url: str


class ListRLRunsInput(MCPModel):
    project_id: str | None = None
    status: (
        Literal[
            "queued",
            "starting",
            "baseline",
            "training",
            "final",
            "succeeded",
            "failed",
            "cancelled",
        ]
        | None
    ) = None
    limit: int = Field(20, ge=1, le=50)


class ListRLRunsOutput(MCPModel):
    summary: str
    runs: list[dict[str, Any]]
