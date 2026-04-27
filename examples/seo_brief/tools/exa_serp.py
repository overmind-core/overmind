from __future__ import annotations

import os
from typing import Any

import httpx


def exa_search_serp(query: str, num_results: int = 5) -> dict[str, Any]:
    """Search the web for a query."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {"results": [], "error": "EXA_API_KEY not set"}
    try:
        resp = httpx.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "query": query,
                "numResults": num_results,
                "useAutoprompt": True,
                "contents": {"text": {"maxCharacters": 600}},
            },
            timeout=25.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "query": query,
            "results": [
                {
                    "rank": i + 1,
                    "title": r.get("title"),
                    "url": r.get("url"),
                    "snippet": (r.get("text") or "")[:500],
                }
                for i, r in enumerate(data.get("results", []))
            ],
        }
    except Exception as e:
        return {"results": [], "error": str(e)}
