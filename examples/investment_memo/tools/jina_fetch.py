from __future__ import annotations

from typing import Any

import httpx


def jina_fetch_url(url: str) -> dict[str, Any]:
    """Fetch a URL and return the readable text."""
    try:
        resp = httpx.get(
            f"https://r.jina.ai/{url}",
            headers={"Accept": "text/plain", "User-Agent": "overclaw-examples/0.1"},
            timeout=30.0,
        )
        resp.raise_for_status()
        text = resp.text
        return {"url": url, "text": text[:8000], "truncated": len(text) > 8000}
    except Exception as e:
        return {"url": url, "text": "", "error": str(e)}
