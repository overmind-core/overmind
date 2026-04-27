"""Incident triage agent (LangChain).

Uses LangChain's tool-calling agent to make OverClaw's framework auto-wrap
path do the work at registration time.

Deliberate sub-optimalities:
- System prompt has no severity calibration (SEV1-SEV4 definitions)
- Agent doesn't correlate deploy window with alert time
- Over-uses SEV1 because "production" appears in most alerts
- Runs `search_runbook` on the raw alert text instead of extracted keywords
- No schema validation on final JSON
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

from dotenv import load_dotenv
from langchain.agents import AgentExecutor, create_tool_calling_agent
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from prompts import SYSTEM_PROMPT
from tools import fetch_commit_diff, list_recent_commits, query_metrics, search_runbook

load_dotenv()

_MODEL = os.environ.get("INCIDENT_TRIAGE_MODEL", "gpt-4o-mini")
_REPO = os.environ.get("INCIDENT_TRIAGE_REPO", "overclaw-demo/platform")


def _llm() -> ChatOpenAI:
    return ChatOpenAI(model=_MODEL, temperature=0.2)


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group(0))
        except Exception:
            pass
    return {
        "severity": "SEV3",
        "likely_root_cause": text[:400],
        "suspected_commits": [],
        "suggested_owner": "oncall",
        "next_steps": [],
    }


def run(input_data: dict[str, Any]) -> dict[str, Any]:
    service = input_data.get("service", "")
    alert = input_data.get("alert_summary", "")
    timestamp = input_data.get("timestamp", "")

    tools = [list_recent_commits, fetch_commit_diff, query_metrics, search_runbook]

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", SYSTEM_PROMPT),
            (
                "human",
                "Service: {service}\nAlert time (UTC): {ts}\n\nAlert:\n{alert}\n\n"
                "Repo for deploy correlation: {repo}\n"
                "Triage and return the JSON.",
            ),
            ("placeholder", "{agent_scratchpad}"),
        ]
    )

    llm = _llm()
    agent = create_tool_calling_agent(llm, tools, prompt)
    executor = AgentExecutor(agent=agent, tools=tools, max_iterations=10, verbose=False)

    result = executor.invoke(
        {
            "service": service,
            "ts": timestamp,
            "alert": alert,
            "repo": _REPO,
        }
    )
    return _extract_json(result.get("output", ""))
