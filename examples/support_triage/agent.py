"""Support ticket triage agent (Anthropic tool use).

Deliberate sub-optimalities:
- System prompt has no priority or escalation rubric
- Tool descriptions are one-liners - the model picks the wrong tool or order
- No tone / length guidance for `suggested_response`
- Uses a large Claude model for what's essentially a classifier
- Prefers `search_public_docs` (web) over `search_kb` (authoritative internal)
- No schema enforcement on the returned JSON
"""

from __future__ import annotations

import json
import os
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

from .prompts import SYSTEM_PROMPT
from .tools import TOOL_FNS, TOOL_SCHEMAS

load_dotenv()

_MODEL = os.environ.get("SUPPORT_TRIAGE_MODEL", "claude-3-5-sonnet-latest")
_MAX_TOOL_ROUNDS = 8


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(text[start : end + 1])
            except Exception:
                pass
        return {
            "category": "other",
            "priority": "P3",
            "escalate": False,
            "suggested_response": text,
            "tags": [],
        }


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    customer_id = input_data.get("customer_id", "")
    subject = input_data.get("subject", "")
    body = input_data.get("body", "")

    user_text = (
        f"Ticket\nCustomer: {customer_id}\nSubject: {subject}\n\n{body}\n\n"
        "Triage this ticket and return the JSON."
    )

    client = Anthropic()
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_text}]

    for _ in range(_MAX_TOOL_ROUNDS):
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=1500,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=messages,
        )
        messages.append({"role": "assistant", "content": resp.content})

        if resp.stop_reason != "tool_use":
            text_parts = [
                b.text for b in resp.content if getattr(b, "type", None) == "text"
            ]
            return _extract_json("\n".join(text_parts))

        tool_results: list[dict[str, Any]] = []
        for block in resp.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            fn = TOOL_FNS.get(block.name)
            try:
                result = (
                    fn(**block.input) if fn else {"error": f"unknown tool {block.name}"}
                )
            except Exception as e:
                result = {"error": str(e)}
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return {
        "category": "other",
        "priority": "P3",
        "escalate": False,
        "suggested_response": "Our team is looking into your ticket and will follow up shortly.",
        "tags": ["max-rounds"],
    }
