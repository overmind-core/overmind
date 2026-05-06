"""Lead qualifier agent.

Classifies an inbound sales lead using company enrichment.

Deliberate sub-optimalities Overmind should be able to improve:
- System prompt has no calibration bands for hot/warm/cold
- Tool descriptions are vague
- Uses an expensive model for a small classification task
- No schema validation; JSON parsing is best-effort
- Always calls exa_search even when the CRM lookup already answered it
"""

from __future__ import annotations

import json
import os
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI
from prompts import SYSTEM_PROMPT
from tools import TOOL_FNS

load_dotenv()

_MODEL = os.environ.get("LEAD_QUALIFIER_MODEL", "gpt-4o")
_MAX_TOOL_ROUNDS = 3

_CATEGORY_SYNONYMS: dict[str, str] = {
    "enterprise": "hot",
    "large": "hot",
    "high": "hot",
    "medium business": "warm",
    "mid-market": "warm",
    "smb": "cold",
    "small": "cold",
    "qualified lead": "warm",
    "unqualified": "cold",
}

_ENRICHED_TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "lookup_company_size",
            "description": (
                "Look up company size and enrichment data from internal CRM. "
                "MUST be called first for every lead before scoring. "
                "The 'domain' parameter must be a bare domain derived from the company name: "
                "lowercase the name, remove common suffixes (Corp, Inc, Industries, LLC, Ltd, Solutions, Technologies, Tech, Co), "
                "remove all spaces and special characters, then append '.com'. "
                "Examples: 'Acme Corp' → 'acme.com', 'Dunder Mifflin' → 'dundermifflin.com', "
                "'Tech Solutions Inc' → 'techsolutions.com', 'Initech Industries' → 'initech.com'. "
                "Returns: employees (int or null), industry (str or null), is_competitor (bool). "
                "Use employees count as the primary scoring signal for lead classification."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain": {
                        "type": "string",
                        "description": (
                            "Bare domain only (e.g., 'acme.com'). "
                            "Do NOT include 'https://', 'www.', or the full company name. "
                            "Derive by: lowercase name → remove Corp/Inc/LLC/Ltd/Industries/Solutions/Technologies/Tech/Co → "
                            "remove spaces → append .com"
                        ),
                    }
                },
                "required": ["domain"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "exa_search_company",
            "description": (
                "Search the web for company information. "
                "ONLY call this if lookup_company_size returned employees: null or missing employee data. "
                "Do NOT call if CRM already returned a non-null employees value. "
                "Do NOT call if is_competitor is true. "
                "Pass the full company name as the query to get the best results."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "company": {
                        "type": "string",
                        "description": "Full company name to search for (e.g., 'Dunder Mifflin Paper Company')",
                    }
                },
                "required": ["company"],
            },
        },
    },
]


def _derive_domain(company_name: str) -> str:
    """Derive a canonical bare domain from a company name.

    Lowercases the name, removes common corporate suffixes, strips
    whitespace and non-alphanumeric characters, then appends '.com'.
    """
    import re

    name = company_name.lower().strip()
    suffixes = [
        r"\bcorporation\b",
        r"\bcorp\b",
        r"\bincorporated\b",
        r"\binc\b",
        r"\bindustries\b",
        r"\bindustry\b",
        r"\blimited\b",
        r"\bltd\b",
        r"\bllc\b",
        r"\bllp\b",
        r"\bsolutions\b",
        r"\btechnologies\b",
        r"\btechnology\b",
        r"\btech\b",
        r"\bgroup\b",
        r"\bholdings\b",
        r"\benterprises\b",
        r"\benterprise\b",
        r"\bco\b",
    ]
    for suffix in suffixes:
        name = re.sub(suffix, "", name)
    name = re.sub(r"[^a-z0-9]", "", name)
    return f"{name}.com"


def _client() -> OpenAI:
    return OpenAI()


def _validate_output(result: dict[str, Any]) -> dict[str, Any]:
    category = str(result.get("category", "")).strip().lower()
    if category not in ("hot", "warm", "cold"):
        category = _CATEGORY_SYNONYMS.get(category, "warm")
    result["category"] = category

    try:
        score = float(result.get("lead_score", 50))
    except (TypeError, ValueError):
        score = 50.0
    score = max(0.0, min(100.0, score))
    result["lead_score"] = int(score)

    if not result.get("reasoning"):
        result["reasoning"] = "No reasoning provided."
    if not result.get("next_action"):
        if category == "hot":
            result["next_action"] = "schedule demo"
        elif category == "cold":
            result["next_action"] = "add to nurture sequence"
        else:
            result["next_action"] = "follow up"

    return result


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        text = text.removeprefix("json")
        text = text.rsplit("```", 1)[0]

    candidates = [text]
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end >= start:
        candidates.append(text[start : end + 1])

    for chunk in candidates:
        try:
            return json.loads(chunk)
        except json.JSONDecodeError:
            continue

    return {
        "category": "warm",
        "lead_score": 50,
        "reasoning": text,
        "next_action": "follow up",
    }


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    company = input_data.get("company_name", "")
    inquiry = input_data.get("inquiry_text", "")
    role = input_data.get("contact_role", "unknown")

    domain = _derive_domain(company)

    user_msg = (
        f"Company: {company}\n"
        f"Derived CRM domain: {domain}\n"
        f"Contact role: {role}\n"
        f"Inquiry: {inquiry}\n\n"
        f'Step 1: Call lookup_company_size with domain="{domain}" to get CRM data. '
        "Step 2: If employees is null, call exa_search_company. "
        "Step 3: Classify the lead using the scoring bands in your instructions."
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
            tools=_ENRICHED_TOOL_SCHEMAS,
            temperature=0.2,
        )
        msg = resp.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))

        if not msg.tool_calls:
            return _validate_output(_extract_json(msg.content or ""))

        for tc in msg.tool_calls:
            fn = TOOL_FNS.get(tc.function.name)
            try:
                args = json.loads(tc.function.arguments or "{}")
                result = fn(**args) if fn else {"error": f"unknown tool {tc.function.name}"}
            except Exception as e:
                result = {"error": str(e)}
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": json.dumps(result),
            })

    return {
        "category": "warm",
        "lead_score": 50,
        "reasoning": "Max tool rounds reached without a final answer.",
        "next_action": "manual review",
    }
