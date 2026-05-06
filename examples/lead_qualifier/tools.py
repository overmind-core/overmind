"""Tools for the lead qualifier agent.

The docstrings / schemas here are deliberately minimal — Overmind should be
able to rewrite them into something clearer and more actionable.
"""

from __future__ import annotations

import csv
import os
from pathlib import Path
from typing import Any

import httpx

_DATA_DIR = Path(__file__).parent / "data"


def lookup_company_size(domain: str) -> dict[str, Any]:
    """Look up company size from internal CRM data."""
    path = _DATA_DIR / "companies.csv"
    key = domain.lower().strip().removeprefix("www.")
    with path.open() as f:
        for row in csv.DictReader(f):
            if row["domain"].lower() == key:
                return {
                    "domain": row["domain"],
                    "employees": int(row["employees"]),
                    "industry": row["industry"],
                    "is_competitor": row["is_competitor"] == "true",
                }
    return {
        "domain": domain,
        "employees": None,
        "industry": None,
        "is_competitor": False,
    }


def exa_search_company(company: str) -> dict[str, Any]:
    """Search the web for a company."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {"results": [], "error": "EXA_API_KEY not set"}
    try:
        resp = httpx.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "query": f"{company} company overview",
                "numResults": 3,
                "useAutoprompt": True,
                "contents": {"text": {"maxCharacters": 500}},
            },
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "results": [
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "snippet": (r.get("text") or "")[:400],
                }
                for r in data.get("results", [])
            ]
        }
    except Exception as e:
        return {"results": [], "error": str(e)}


TOOL_SCHEMAS = [
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


TOOL_FNS = {
    "lookup_company_size": lookup_company_size,
    "exa_search_company": exa_search_company,
}
