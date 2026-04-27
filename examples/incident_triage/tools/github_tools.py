"""GitHub API tools (public endpoints - no key required, but rate-limited).

Set GITHUB_TOKEN to raise the rate limit.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import httpx
from langchain_core.tools import tool

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
_USE_FIXTURES = os.environ.get("OVERCLAW_USE_FIXTURES") == "1"


def _headers() -> dict[str, str]:
    h = {"Accept": "application/vnd.github+json", "User-Agent": "overclaw-examples"}
    tok = os.environ.get("GITHUB_TOKEN")
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


@tool
def list_recent_commits(repo: str, since_iso: str) -> str:
    """List recent commits."""
    if _USE_FIXTURES:
        p = _FIXTURES / "github" / f"{repo.replace('/', '_')}_commits.json"
        if p.exists():
            return p.read_text()
        return json.dumps({"commits": []})
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo}/commits",
            headers=_headers(),
            params={"since": since_iso, "per_page": 10},
            timeout=20.0,
        )
        resp.raise_for_status()
        commits = [
            {
                "sha": c["sha"][:10],
                "message": c["commit"]["message"].splitlines()[0],
                "author": c["commit"]["author"]["name"],
                "date": c["commit"]["author"]["date"],
            }
            for c in resp.json()
        ]
        return json.dumps({"commits": commits})
    except Exception as e:
        return json.dumps({"commits": [], "error": str(e)})


@tool
def fetch_commit_diff(repo: str, sha: str) -> str:
    """Fetch a commit diff."""
    if _USE_FIXTURES:
        p = _FIXTURES / "github" / f"{repo.replace('/', '_')}_{sha}.diff"
        if p.exists():
            return p.read_text()[:3000]
        return "no fixture"
    try:
        resp = httpx.get(
            f"https://api.github.com/repos/{repo}/commits/{sha}",
            headers={**_headers(), "Accept": "application/vnd.github.diff"},
            timeout=20.0,
        )
        resp.raise_for_status()
        return resp.text[:3000]
    except Exception as e:
        return f"error: {e}"
