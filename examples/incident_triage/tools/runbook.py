from __future__ import annotations

import json
from pathlib import Path

from langchain_core.tools import tool

_FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "runbooks.json"


@tool
def search_runbook(query: str) -> str:
    """Search the runbook."""
    if not _FIXTURE.exists():
        return json.dumps({"results": []})
    books = json.loads(_FIXTURE.read_text())
    q = query.lower()
    scored = []
    for rb in books:
        hay = (
            rb["title"] + " " + rb["body"] + " " + " ".join(rb.get("tags", []))
        ).lower()
        score = sum(1 for tok in q.split() if tok in hay)
        if score:
            scored.append((score, rb))
    scored.sort(key=lambda x: -x[0])
    return json.dumps(
        {
            "results": [
                {"id": rb["id"], "title": rb["title"], "snippet": rb["body"][:280]}
                for _, rb in scored[:3]
            ]
        }
    )
