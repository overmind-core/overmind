import time

from overbae.core.llms import ToolStreamResult
from overbae.services import chatgpt
from overbae.services.datasets.notebook.engines.native import DELTA_FLUSH_SECONDS, NativeEngine


def response_input(messages):
    items = []
    for message in messages:
        replay = next(
            (
                part["output"]
                for part in message.get("reasoning_details", [])
                if part.get("type") == "chatgpt_response"
            ),
            None,
        )
        if replay is not None:
            items.extend(replay)
            continue
        role = message.get("role")
        if role == "tool":
            items.append(
                {
                    "type": "function_call_output",
                    "call_id": message["tool_call_id"],
                    "output": message["content"],
                }
            )
            continue
        if role in {"user", "assistant"} and message.get("content"):
            items.append({"role": role, "content": message["content"]})
        for call in message.get("tool_calls", []):
            items.append(
                {
                    "type": "function_call",
                    "call_id": call["id"],
                    "name": call["function"]["name"],
                    "namespace": "workshop",
                    "arguments": call["function"]["arguments"],
                }
            )
    return items


class ChatGPTEngine(NativeEngine):
    name = "chatgpt"

    def __init__(self, session):
        self.session = session
        self.usage = {"served_model": session.model}

    def _round(self, system, messages, schemas, tools, pending):
        tools.start_response()
        instructions = "\n".join(part["text"] for part in system["content"])
        streamed = False
        buffer, thoughts = [], []
        flushed_at = time.monotonic()
        for event in chatgpt.response_stream(
            self.session, response_input(messages), instructions=instructions, tools=schemas
        ):
            kind = event["type"]
            if kind == "response.output_text.delta":
                buffer.append(event.get("delta", ""))
                streamed = True
            elif kind == "response.reasoning_summary_text.delta":
                thoughts.append(event.get("delta", ""))
            if kind == "response.completed" or time.monotonic() - flushed_at >= DELTA_FLUSH_SECONDS:
                flushed_at = time.monotonic()
                if thoughts:
                    tools.thought("".join(thoughts))
                    thoughts.clear()
                if buffer:
                    tools.stop_thinking()
                    tools.respond("".join(buffer))
                    buffer.clear()
            while pending:
                yield pending.pop(0)
            if kind != "response.completed":
                continue
            response = event["response"]
            stats = chatgpt.response_usage(response)
            self.usage["served_model"] = stats.get("served_model") or self.session.model
            for key in ("prompt_tokens", "completion_tokens"):
                self.usage[key] = self.usage.get(key, 0) + stats[key]
            text = chatgpt.output_text(response)
            if not streamed and text:
                tools.stop_thinking()
                tools.respond(text)
                while pending:
                    yield pending.pop(0)
            output = response.get("output", [])
            calls = []
            for item in output:
                if item.get("type") == "function_call":
                    if item.get("namespace") not in {None, "workshop"}:
                        raise chatgpt.ChatGPTError("ChatGPT returned an unknown tool namespace.")
                    calls.append(
                        {
                            "id": item["call_id"],
                            "type": "function",
                            "function": {"name": item["name"], "arguments": item["arguments"]},
                        }
                    )
            return ToolStreamResult(
                text=text,
                tool_calls=calls,
                stats=stats,
                reasoning_details=[{"type": "chatgpt_response", "output": output}],
            )
        raise chatgpt.ChatGPTError("ChatGPT did not complete this response.")

    def describe_error(self, exc):
        if isinstance(exc, chatgpt.ChatGPTError):
            return str(exc)
        return "The ChatGPT Workshop turn could not finish. Try again later."
