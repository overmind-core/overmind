from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Protocol

from overbae.core.model_registry import WORKSHOP_KEY_ENVS, workshop_engine
from overbae.models import Dataset

NOT_CONFIGURED = (
    "No agent is configured on this server. Set one of " + ", ".join(WORKSHOP_KEY_ENVS) + "."
)


@dataclass
class Outcome:
    text: str = ""
    error: str = ""
    stats: dict[str, Any] = field(default_factory=dict)


class Engine(Protocol):
    name: str

    def run(
        self, dataset: Dataset, message: str, tools: Any, pending: list[dict[str, Any]]
    ) -> Generator[dict[str, Any], None, Outcome]: ...

    def describe_error(self, exc: Exception) -> str: ...


def select() -> Engine | None:
    choice = workshop_engine()
    if choice is None:
        return None
    if choice.provider.name == "cursor":
        from overbae.services.datasets.notebook.engines.cursor import CursorEngine

        return CursorEngine(choice)
    from overbae.services.datasets.notebook.engines.native import NativeEngine

    return NativeEngine(choice)
