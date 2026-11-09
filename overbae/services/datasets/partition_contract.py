from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HoldoutRule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    field: str = Field(min_length=1, max_length=200)
    values: list[str | int | float | bool | None] = Field(min_length=1)
    role: Literal["train", "development", "calibration", "final"]


class PartitionRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    seed: int = Field(ge=0, le=4294967295)
    fractions: dict[Literal["train", "development", "calibration", "final"], float]
    group_by: list[str] = Field(default_factory=list, max_length=20)
    stratify_by: str | None = None
    holdouts: list[HoldoutRule] = Field(default_factory=list, max_length=20)
