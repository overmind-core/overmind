from __future__ import annotations

import os
from typing import Any

import httpx


def exa_search_news(query: str) -> dict[str, Any]:
    """Search news and web for a query."""
    api_key = os.environ.get("EXA_API_KEY")
    if not api_key:
        return {"results": [], "error": "EXA_API_KEY not set"}
    try:
        resp = httpx.post(
            "https://api.exa.ai/search",
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            json={
                "query": query,
                "numResults": 5,
                "useAutoprompt": True,
                "category": "news",
                "contents": {"text": {"maxCharacters": 800}},
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
                    "published_date": r.get("publishedDate"),
                    "snippet": (r.get("text") or "")[:600],
                }
                for r in data.get("results", [])
            ]
        }
    except Exception as e:
        return {"results": [], "error": str(e)}
