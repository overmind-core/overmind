"""Explicit data boundaries for internal analytics and Slack delivery."""

from __future__ import annotations

import re

EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+")
_CREDENTIAL = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----"
    r"|\b(?:sk[-_]|rk_live_|gh[pousr]_|github_pat_|xox[baprs]-|phx_|phs_|ovr_)[A-Za-z0-9_\-]{8,}"
    r"|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"
    r"|\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"
    r"|(?i:\b(?:api[_ -]?key|authorization|password|secret|token)\s*[:=]\s*\S+)"
)


def model_question(question: str) -> tuple[str, dict[str, str]]:
    """Reject credential-like input; replace emails with request-local references."""
    if _CREDENTIAL.search(question):
        raise ValueError("credential_like_input")
    identities: dict[str, str] = {}

    def replace(match: re.Match) -> str:
        email = match.group().lower()
        for reference, existing in identities.items():
            if existing == email:
                return reference
        reference = f"customer_{len(identities) + 1}"
        identities[reference] = email
        return reference

    question = re.sub(r"<mailto:([^|>]+)(?:\|[^>]+)?>", r"\1", question)
    question = EMAIL.sub(replace, question)
    question = re.sub(r"https?://\S+", "[link omitted]", question)
    question = re.sub(r"<[@#!][^>]+>", "[Slack reference omitted]", question)
    return question, identities


def posthog_url(host: str, path: str, *, ingest: bool = False) -> str:
    """Never send a read key or customer identifier to a redirect or arbitrary host."""
    allowed = (
        {"https://eu.i.posthog.com", "https://us.i.posthog.com"}
        if ingest
        else {"https://eu.posthog.com", "https://us.posthog.com"}
    )
    host = host.rstrip("/")
    if host not in allowed:
        raise ValueError("unapproved_posthog_host")
    return host + path
