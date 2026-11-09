import json
import os
import time
from queue import Empty, Full, Queue
from threading import Event, Thread

import httpx
from django.conf import settings
from openai import OpenAI
from pydantic_core import from_json

from overbae.core.model_registry import TaskType, resolve_model, resolve_openrouter_slug
from overbae.models import WorkshopTask
from overbae.services.workshop_agent.conversation import emit


class UnknownResponseError(Exception):
    pass


def configured_model():
    name = os.environ.get("WORKSHOP_AGENT_MODEL") or resolve_model(TaskType.WORKSHOP_AGENT)
    return name if "/" in name else resolve_openrouter_slug(name)


def matches_retained(value, retained):
    if isinstance(value, dict):
        return isinstance(retained, dict) and all(
            key in retained and matches_retained(item, retained[key]) for key, item in value.items()
        )
    if isinstance(value, list):
        return (
            isinstance(retained, list)
            and len(value) == len(retained)
            and all(
                matches_retained(item, saved) for item, saved in zip(value, retained, strict=True)
            )
        )
    return value == retained


class ModelStream:
    def __init__(self, *, url="https://openrouter.ai/api/v1", api_key=None):
        self.client = OpenAI(
            base_url=url,
            api_key=api_key or settings.OPENROUTER_API_KEY,
            max_retries=0,
            timeout=httpx.Timeout(150, connect=15),
        )
        self.task_id = None
        self.outputs = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.client.close()

    def context(self, task, items):
        if self.task_id is None:
            self.task_id = task.pk
            for receipt in task.actions.filter(name="reason", state="completed").values_list(
                "receipt", flat=True
            ):
                for item in receipt.get("response", {}).get("body", {}).get("output", []):
                    if item.get("id"):
                        self.outputs[item["id"]] = item
        if self.task_id != task.pk:
            raise ValueError("A model connection belongs to one Workshop task")
        # Retain provider phase and encrypted reasoning fields that message adapters may omit.
        result = []
        for item in items:
            retained = self.outputs.get(item.get("id"))
            if retained is not None and not matches_retained(
                {key: value for key, value in item.items() if key != "status"}, retained
            ):
                raise ValueError("Model history conflicts with a retained response")
            result.append(retained if retained is not None else item)
        return result

    def complete(self, task, request):
        arguments = {key: value for key, value in request["arguments"].items() if key != "stream"}
        arguments.update(store=False, service_tier="default")
        full_input = self.context(task, arguments["input"])
        arguments["input"] = full_input
        started = time.monotonic()
        events = Queue(maxsize=64)
        stopped = Event()
        streams = []

        def deliver(value):
            while not stopped.is_set():
                try:
                    events.put(value, timeout=0.2)
                    return
                except Full:
                    pass

        def receive():
            try:
                with self.client.responses.create(**arguments, stream=True) as stream:
                    streams.append(stream)
                    for event in stream:
                        if stopped.is_set():
                            break
                        deliver(event.model_dump(mode="json", exclude_unset=True))
                deliver(UnknownResponseError("Provider stream ended without a complete response"))
            except Exception as exc:
                deliver(exc)

        Thread(target=receive, daemon=True).start()
        message = {
            "id": f"model:{request['turn']}",
            "role": "assistant",
            "text": "",
            "status": "streaming",
        }
        last_flush = last_control = last_action_flush = 0
        tool_name = tool_arguments = ""
        last_action = None
        revision = 0
        first_event = None

        def flush_action(status="preparing"):
            nonlocal last_action, revision, last_action_flush
            if not tool_name:
                return
            try:
                values = from_json(tool_arguments, allow_partial="trailing-strings")
            except ValueError:
                values = {}
            if not isinstance(values, dict):
                values = {}
            summary = values.get("summary")
            action = {
                "id": f"action:{request['turn']}",
                "role": "activity",
                "tool": tool_name,
                "text": summary[:240]
                if isinstance(summary, str) and summary
                else f"Preparing {tool_name}",
                "status": status,
            }
            code = values.get("code")
            if tool_name == "python" and isinstance(code, str):
                action["code"] = code
            elif tool_name != "python":
                inputs = {key: value for key, value in values.items() if key != "summary"}
                if inputs:
                    action["code"] = json.dumps(inputs, ensure_ascii=False, indent=2)
            if action != last_action:
                revision += 1
                emit(task, f"model:{request['turn']}:action:{revision}", action)
                last_action = dict(action)
                last_action_flush = time.monotonic()

        try:
            while True:
                now = time.monotonic()
                if now - last_control >= 0.2:
                    last_control = now
                    if WorkshopTask.objects.filter(pk=task.pk, desired_state="cancelled").exists():
                        flush_action("interrupted")
                        emit(
                            task,
                            f"model:{request['turn']}:interrupted",
                            {**message, "status": "interrupted"},
                        )
                        return {"cancelled": True}
                if now - started > 150:
                    raise UnknownResponseError("Model response deadline exceeded")
                try:
                    event = events.get(timeout=0.2)
                except Empty:
                    continue
                if isinstance(event, Exception):
                    raise event
                first_event = first_event if first_event is not None else time.monotonic() - started
                kind = event["type"]
                if kind == "error":
                    error = event.get("error", event)
                    return {"status": event.get("status", 400), "body": {"error": error}}
                elif kind == "response.output_text.delta":
                    message["text"] += event["delta"]
                    if now - last_flush >= 0.15:
                        emit(task, f"model:{request['turn']}:{len(message['text'])}", message)
                        last_flush = now
                elif (
                    kind == "response.output_item.added"
                    and event["item"]["type"] == "function_call"
                ):
                    tool_name = event["item"]["name"]
                    tool_arguments = event["item"].get("arguments", "")
                    flush_action()
                elif kind == "response.function_call_arguments.delta":
                    tool_arguments += event["delta"]
                    if now - last_action_flush >= 0.15:
                        flush_action()
                elif kind == "response.function_call_arguments.done":
                    tool_arguments = event["arguments"]
                    flush_action()
                elif kind in {"response.failed", "response.incomplete"}:
                    raise ValueError(
                        "The model response ended without a complete answer or action."
                    )
                elif kind == "response.completed":
                    body = event["response"]
                    if body.get("status") != "completed":
                        raise ValueError("The model response did not complete.")
                    self.outputs.update(
                        {item["id"]: item for item in body["output"] if item.get("id")}
                    )
                    flush_action()
                    emit(
                        task,
                        f"model:{request['turn']}:complete",
                        {**message, "status": "completed"},
                    )
                    return {
                        "status": 200,
                        "body": body,
                        "transport": {
                            "first_event_seconds": first_event,
                            "response_seconds": time.monotonic() - started,
                            "sent_bytes": len(json.dumps(arguments).encode()),
                        },
                    }
        except Exception as exc:
            flush_action("interrupted")
            emit(task, f"model:{request['turn']}:interrupted", {**message, "status": "interrupted"})
            if isinstance(exc, (httpx.TransportError, OSError, TimeoutError)):
                raise UnknownResponseError("Model acknowledgement lost") from exc
            raise
        finally:
            stopped.set()
            for stream in streams:
                stream.close()
