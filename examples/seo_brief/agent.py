"""SEO content brief generator.

Deliberate sub-optimalities:
- Prompt doesn't require SERP grounding
- No intent taxonomy (informational/commercial/transactional/navigational)
- Agent often fetches 5+ URLs needlessly — unbounded exploration
- No word-count calibration against the SERP
- JSON output not validated
"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from prompts import SYSTEM_PROMPT
from tools import TOOL_FNS, TOOL_SCHEMAS

load_dotenv()

_MODEL = os.environ.get("SEO_BRIEF_MODEL", "gpt-4o")
_MAX_TOOL_ROUNDS = 12


def _client() -> OpenAI:
    return OpenAI()


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
        "search_intent": "informational",
        "outline": [],
        "target_word_count": 1200,
        "serp_gaps": [],
        "faqs": [],
    }


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    keyword = input_data.get("keyword", "")
    audience = input_data.get("audience", "general")
    existing = input_data.get("existing_urls", [])

    user_msg = (
        f"Keyword: {keyword}\nAudience: {audience}\n"
        f"Existing URLs (if any) to differentiate from: {existing}\n\n"
        "Produce the content brief JSON."
    )

    client = _client()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg},
    ]

    for _ in range(_MAX_TOOL_ROUNDS):
        resp = client.chat.completions.create(
            model=_MODEL,
            messages=messages,
            tools=TOOL_SCHEMAS,
            temperature=0.3,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            return _extract_json(msg.content or "")

        for tc in msg.tool_calls:
            fn = TOOL_FNS.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = (
                    fn(**args) if fn else {"error": f"unknown tool {tc.function.name}"}
                )
            except Exception as e:
                result = {"error": str(e)}
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": json.dumps(result)[:5000],
                }
            )

    return {
        "search_intent": "informational",
        "outline": [],
        "target_word_count": 1200,
        "serp_gaps": [],
        "faqs": [],
    }
