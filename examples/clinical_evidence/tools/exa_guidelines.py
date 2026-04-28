from __future__ import annotations

import os
from typing import Any

import httpx


def exa_search_guidelines(query: str) -> dict[str, Any]:
    """Search the web for clinical guidelines."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {"results": [], "error": "EXA_API_KEY not set"}
    try:
        resp = httpx.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "query": query + " clinical practice guideline",
                "numResults": 4,
                "useAutoprompt": True,
                "contents": {"text": {"maxCharacters": 700}},
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "results": [
                {
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "snippet": (r.get("text") or "")[:500],
                }
                for r in data.get("results", [])
            ]
        }
    except Exception as e:
        return {"results": [], "error": str(e)}
