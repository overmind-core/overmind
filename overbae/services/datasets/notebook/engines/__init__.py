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


def _wrap(choice) -> Engine:
    if choice.provider.name == "cursor":
        from overbae.services.datasets.notebook.engines.cursor import CursorEngine

        return CursorEngine(choice)
    from overbae.services.datasets.notebook.engines.native import NativeEngine

    return NativeEngine(choice)


def select() -> Engine | None:
    choice = workshop_engine()
    return None if choice is None else _wrap(choice)


def ordered() -> list[Engine]:
    from overbae.core.model_registry import WORKSHOP_ENGINES

    return [_wrap(choice) for choice in WORKSHOP_ENGINES if choice.provider.configured()]
