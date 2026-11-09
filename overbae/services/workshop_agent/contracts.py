from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from workshop_runtime.presentation import Interaction


class StartTask(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: UUID
    context_datasets: list[UUID] = Field(default_factory=list, max_length=100)
    objective: str = Field(min_length=1, max_length=16000)
    request_key: str = Field(min_length=1, max_length=128)
    max_steps: int = Field(
        default=0,
        ge=0,
        le=500,
        description="Action limit; zero disables it.",
    )


class SendMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: UUID
    text: str = Field(min_length=1, max_length=16000)
    request_key: str = Field(min_length=1, max_length=128)
    cell: UUID | None = None
    interaction: Interaction | None = None


class ControlMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    task: UUID
    message: str = Field(min_length=1, max_length=180)
    command: Literal["edit", "remove", "move"]
    position: int | None = Field(default=None, ge=0)
    text: str | None = Field(default=None, min_length=1, max_length=16000)
    revision: int = Field(ge=0)
    request_key: str = Field(min_length=1, max_length=128)
