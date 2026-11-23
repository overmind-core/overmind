"""Phi-4 tool rows. The template ignores tool_calls."""

from __future__ import annotations

import json

from families import DefaultHooks


class Phi4Hooks(DefaultHooks):
    def normalize_messages(
        self, messages: list[dict], tools: list[dict] | None
    ) -> tuple[list[dict], list[dict] | None]:
        if tools is None:
            return messages, tools
        # system.tools is a JSON string. Assistant content is {name, arguments}.
        # A tool role's content is the result JSON. Tools are not also a kwarg.
        msgs: list[dict] = []
        tools_json = json.dumps(tools)
        has_system = False
        for raw in messages:
            message = dict(raw)
            role = message.get("role")
            if role == "system":
                has_system = True
                message["tools"] = tools_json
                msgs.append(message)
                continue
            if role == "assistant" and message.get("tool_calls"):
                call = message["tool_calls"][0]
                function = call.get("function") or call
                msgs.append(
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "name": function.get("name"),
                                "arguments": function.get("arguments", {}),
                            }
                        ),
                    }
                )
                continue
            if role == "tool":
                msgs.append({"role": "tool", "content": message.get("content", "")})
                continue
            msgs.append({key: value for key, value in message.items() if key != "tool_calls"})
        if not has_system:
            msgs.insert(
                0,
                {
                    "role": "system",
                    "content": "You are a helpful assistant.",
                    "tools": tools_json,
                },
            )
        return msgs, None


hooks = Phi4Hooks()
