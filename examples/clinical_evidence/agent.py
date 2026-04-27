"""Clinical evidence Q&A agent.

Answers clinical questions with a GRADE-like evidence level.

Deliberate sub-optimalities:
- Prompt doesn't mandate a disclaimer
- No evidence-hierarchy rule (SR > RCT > observational > case report)
- Agent sometimes cites PMIDs without fetching the abstract first (so it may
  fabricate)
- Uses a small Claude Haiku model for a task that benefits from stronger
  reasoning (model-selection *upgrade* candidate)
- Evidence grade is emitted without justification
"""

from __future__ import annotations

import json
import os
from typing import Any

from anthropic import Anthropic
from dotenv import load_dotenv

from prompts import SYSTEM_PROMPT
from tools import TOOL_FNS, TOOL_SCHEMAS

load_dotenv()

_MODEL = os.environ.get("CLINICAL_EVIDENCE_MODEL", "claude-sonnet-4-6")
_MAX_TOOL_ROUNDS = 12


def _client() -> Anthropic:
    return Anthropic()


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
        "answer": text,
        "evidence_grade": "D",
        "key_studies": [],
        "caveats": [],
        "disclaimer": "",
    }


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    question = input_data.get("clinical_question", "")
    population = input_data.get("population", "")

    user_text = (
        f"Clinical question: {question}\n"
        f"Population: {population or 'general adult population'}\n\n"
        "Answer with a JSON evidence-graded response."
    )

    client = _client()
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_text}]

    for _ in range(_MAX_TOOL_ROUNDS):
        resp = client.messages.create(
            model=_MODEL,
            max_tokens=2000,
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
                    "content": json.dumps(result)[:5000],
                }
            )
        messages.append({"role": "user", "content": tool_results})

    return {
        "answer": "Evidence synthesis did not converge.",
        "evidence_grade": "D",
        "key_studies": [],
        "caveats": ["tool-budget exhausted"],
        "disclaimer": "",
    }
