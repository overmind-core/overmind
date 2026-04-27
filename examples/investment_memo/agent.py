"""Investment thesis memo agent.

Takes a ticker + question, writes a structured memo.

Deliberate sub-optimalities:
- Iteration limit is too generous (16 rounds) - leads to tool loops
- Prompt doesn't require citations or fact/opinion separation
- Tool descriptions don't distinguish "use EDGAR for financials" vs "use news for catalysts"
- Uses o1-mini which is inappropriate for tool-calling workflows
- No de-duplication of citations
- Horizon (short vs long) is ignored
"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from .prompts import SYSTEM_PROMPT
from .tools import TOOL_FNS, TOOL_SCHEMAS

load_dotenv()

_MODEL = os.environ.get("INVESTMENT_MEMO_MODEL", "gpt-4o")
_MAX_TOOL_ROUNDS = 16


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
        "thesis": text,
        "key_drivers": [],
        "risks": [],
        "valuation_notes": "",
        "citations": [],
        "confidence": "low",
    }


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    ticker = input_data.get("ticker", "").upper()
    question = input_data.get("thesis_question", "")
    horizon = input_data.get("horizon", "long")

    user_msg = (
        f"Ticker: {ticker}\nHorizon: {horizon}\nQuestion: {question}\n\n"
        "Write the investment memo JSON."
    )

    client = OpenAI()
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
                    "content": json.dumps(result)[:6000],
                }
            )

    return {
        "thesis": "Investigation did not converge within the iteration budget.",
        "key_drivers": [],
        "risks": [],
        "valuation_notes": "",
        "citations": [],
        "confidence": "low",
    }
