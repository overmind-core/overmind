from __future__ import annotations

import logging
from collections.abc import Callable, Generator
from typing import Any

from cursor_sdk import SendOptions
from django.db import close_old_connections

from overbae.core.model_registry import Engine as EngineChoice
from overbae.models import Dataset
from overbae.services.datasets import operations
from overbae.services.datasets.notebook import workspace
from overbae.services.datasets.notebook.engines import Outcome

logger = logging.getLogger(__name__)


def _on_sdk_thread(handler: Callable[..., Any]) -> Callable[..., Any]:
    # The Cursor SDK runs each call on a fresh thread; no request_finished reaches it.
    def execute(args: dict[str, Any], ctx: Any = None) -> Any:
        try:
            return handler(args, ctx)
        finally:
            close_old_connections()

    return execute


def _stats(usage: Any, model: str) -> dict[str, Any]:
    from overbae.services.cursor_usage import token_usage_dict

    tokens = token_usage_dict(usage)
    if not tokens:
        return {}
    cached = int(tokens.get("cache_read_tokens") or 0)
    return {
        "prompt_tokens": int(tokens.get("input_tokens") or 0) + cached,
        "completion_tokens": int(tokens.get("output_tokens") or 0),
        "cached_tokens": cached,
        "served_model": model,
    }


class CursorEngine:
    def __init__(self, choice: EngineChoice) -> None:
        self.choice = choice
        self.name = choice.provider.name

    def _options(self, dataset: Dataset, tools: Any) -> Any:
        from cursor_sdk import AgentOptions, CustomTool, LocalAgentOptions, LocalAgentStoreConfig

        from overbae.services.datasets.notebook.agent import TOOL_SPECS, system_prompt

        root = workspace.prepare(dataset, system_prompt(dataset))
        store_dir = root / ".agent"
        store_dir.mkdir(exist_ok=True)
        handlers = tools.handlers()
        custom_tools = {
            name: CustomTool(
                execute=_on_sdk_thread(handlers[name]), description=description, input_schema=schema
            )
            for name, (description, schema) in TOOL_SPECS.items()
        }
        return AgentOptions(
            api_key=self.choice.provider.key(),
            model=self.choice.model,
            name=f"dataset-{dataset.id}",
            tools=["mcp"],
            disallowed_tools=["shell", "task"],
            local=LocalAgentOptions(
                cwd=str(root),
                setting_sources=["project"],
                store=LocalAgentStoreConfig(type="sqlite", root_dir=str(store_dir)),
                custom_tools=custom_tools,
            ),
        )

    @staticmethod
    def _open(dataset: Dataset, options: Any) -> Any:
        from cursor_sdk import Agent

        if dataset.agent_id:
            try:
                return Agent.resume(dataset.agent_id, options)
            except Exception:  # noqa: BLE001 — a lost session starts a fresh one
                logger.warning("dataset %s: agent resume failed", dataset.id, exc_info=True)
        return Agent.create(options=options)

    def run(
        self, dataset: Dataset, message: str, tools: Any, pending: list[dict[str, Any]]
    ) -> Generator[dict[str, Any], None, Outcome]:
        def on_delta(update: Any) -> None:
            if update.type == "step-started":
                tools.start_response()
            elif update.type == "text-delta":
                tools.respond(update.text)
            elif update.type == "thinking-delta":
                tools.thought(update.text)
            elif update.type == "thinking-completed":
                tools.stop_thinking(duration_ms=update.thinking_duration_ms)

        outcome = Outcome()
        options = self._options(dataset, tools)
        with self._open(dataset, options) as agent:
            if agent.agent_id != dataset.agent_id:
                Dataset.objects.filter(pk=dataset.pk).update(agent_id=agent.agent_id)
            # The bridge rejects an idempotency_key on a local agent's Send.
            operations.provider_submitting(dataset.pk, dataset.agent_turn_key, agent.agent_id)
            run = agent.send(
                system_prompt_for_turn(dataset, message), SendOptions(on_delta=on_delta)
            )
            finished = False
            try:
                operations.provider_started(
                    dataset.pk, dataset.agent_turn_key, agent.agent_id, run.id
                )
                for _item in run.stream():
                    operations.check_cancelled(dataset.pk, task_id=dataset.agent_turn_key)
                    while pending:
                        yield pending.pop(0)
                result = run.wait()
                finished = True
                operations.change(
                    dataset.pk,
                    owner_id=dataset.agent_turn_key,
                    provider={"agent_id": agent.agent_id, "run_id": run.id, "state": "completed"},
                )
            finally:
                # Closing a local SDK handle does not clear an active provider run.
                if not finished:
                    try:
                        run.cancel()
                        operations.change(
                            dataset.pk,
                            owner_id=dataset.agent_turn_key,
                            provider={
                                "agent_id": agent.agent_id,
                                "run_id": run.id,
                                "state": "cancelled",
                            },
                        )
                    except Exception:  # noqa: BLE001 — retain the original interruption
                        logger.warning(
                            "dataset %s: provider cancellation failed", dataset.id, exc_info=True
                        )
            if str(getattr(result, "status", "")).lower() == "error":
                outcome.error = "The agent stopped with an error."
            outcome.stats = _stats(getattr(run, "usage", None), self.choice.model)
        outcome.text = tools.text
        return outcome

    def describe_error(self, exc: Exception) -> str:
        from cursor_sdk import (
            AgentBusyError,
            APITimeoutError,
            AuthenticationError,
            PermissionDeniedError,
            RateLimitError,
        )

        if isinstance(exc, AgentBusyError):
            return "The agent is still working on the previous message."
        if isinstance(exc, RateLimitError):
            return "Rate limited by the model provider. Try again in a moment."
        if isinstance(exc, APITimeoutError):
            return "The model provider timed out."
        if isinstance(exc, PermissionDeniedError | AuthenticationError):
            logger.error("cursor engine: credentials rejected", exc_info=exc)
            return "The model provider rejected this server's CURSOR_API_KEY."
        return f"The agent could not finish: {exc}"[:400]


def system_prompt_for_turn(dataset: Dataset, message: str) -> str:
    from overbae.services.datasets.notebook.agent import system_prompt

    # Custom-tools-only sessions cannot read the workspace instructions themselves.
    return f"{system_prompt(dataset)}\n\n## Current request\n\n{message}"
