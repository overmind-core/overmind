from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass, field
from typing import Any, Protocol

from overbae.core.model_registry import WORKSHOP_KEY_ENVS, workshop_engine
from overbae.models import Dataset
from overbae.services.chatgpt import selected_session

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


def select(user=None) -> Engine | None:
    session = selected_session(user)
    if session is not None:
        # The engine protocol lives here; importing adapters eagerly creates a cycle.
        from overbae.services.datasets.notebook.engines.chatgpt import ChatGPTEngine

        return ChatGPTEngine(session)
    choice = workshop_engine()
    if choice is None:
        return None
    if choice.provider.name == "cursor":
        from overbae.services.datasets.notebook.engines.cursor import CursorEngine

        return CursorEngine(choice)
    from overbae.services.datasets.notebook.engines.native import NativeEngine

    return NativeEngine(choice)
