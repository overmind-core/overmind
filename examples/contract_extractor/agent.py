"""Contract clause extractor.

Intentionally single-file to demonstrate that OverClaw helps even for
tool-less, long-context prompt agents. OverClaw has to earn its score
entirely from system-prompt / output-schema rewrites here.

Deliberate sub-optimalities:
- Prompt asks for fields but doesn't require evidence spans
- No date-format normalisation rule
- `red_flags` is open-ended; model may skip auto-renewal clauses
- No fallback for missing fields (sometimes emits "N/A" strings, sometimes null)
- Uses gpt-4o for all cases - could be tiered by document length
"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

_MODEL = os.environ.get("CONTRACT_EXTRACTOR_MODEL", "gpt-4o")

SYSTEM_PROMPT = """You are a paralegal. Extract structured information from the contract below.

Return a JSON with:
- parties (list)
- effective_date
- term
- termination (list of strings)
- liability_cap
- auto_renewal
- ip_assignment
- red_flags (list)

Use the contract text only.
"""


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
        "parties": [],
        "effective_date": None,
        "term": None,
        "termination": [],
        "liability_cap": None,
        "auto_renewal": None,
        "ip_assignment": None,
        "red_flags": [text[:200]],
    }


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    contract_text = input_data.get("contract_text", "")
    clause_types = input_data.get("clause_types", [])

    user_msg = (
        f"Contract:\n{contract_text[:20000]}\n\n"
        f"Clause types of interest: {', '.join(clause_types) if clause_types else 'all standard'}\n"
        "Return the extraction JSON."
    )

    client = OpenAI()
    resp = client.chat.completions.create(
        model=_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
        ],
        temperature=0.1,
    )
    return _extract_json(resp.choices[0].message.content or "")
