# Deep-research POC instrumented with decorator-declared capability graph.
#   uv run python kimm.py "your question"

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import overmind
from overmind import Expectation, capability, observe, task

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env", override=True)

MODEL = "openai/gpt-5-mini"
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPENROUTER_API_KEY"],
)

WIKI_UA = "overmind-random-research-poc/0.1 (local script)"

RESEARCH_PROMPT = (
    "Break the question into exactly 2 short Wikipedia search queries. "
    "Reply with a JSON array of strings and nothing else."
)
DIG_PROMPT = (
    "You research one query. Call wiki_search, then wiki_read on the best "
    "title (you may read a second page). Then stop calling tools and write "
    "3 to 5 factual bullets. Name the page title in each bullet."
)
BRIEF_PROMPT = (
    "Write a short research brief from the notes. Plain prose, under 200 words. "
    "Mention which Wikipedia pages the claims came from. If the notes disagree "
    "or are thin, say so in one sentence."
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "wiki_search",
            "description": "Search English Wikipedia. Returns titles and one-line descriptions.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wiki_read",
            "description": "Read the lead summary of one Wikipedia page by exact title.",
            "parameters": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
    },
]


def _get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": WIKI_UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", errors="replace")


@observe(type="tool")
def wiki_search(query: str) -> str:
    """Search English Wikipedia for titles matching the query."""
    q = urllib.parse.quote(query)
    url = f"https://en.wikipedia.org/w/api.php?action=opensearch&search={q}&limit=4&namespace=0&format=json"
    try:
        data = json.loads(_get(url))
        titles = data[1]
        blurbs = data[2]
        lines = []
        for i, title in enumerate(titles):
            blurb = blurbs[i] if i < len(blurbs) else ""
            lines.append(f"- {title}: {blurb}")
        return "\n".join(lines) if lines else "no hits"
    except Exception as e:
        return f"search failed: {e}"


@observe(type="tool")
def wiki_read(title: str) -> str:
    """Read the lead summary of one Wikipedia page by exact title."""
    slug = urllib.parse.quote(title.replace(" ", "_"), safe="")
    url = f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}"
    try:
        data = json.loads(_get(url))
        extract = data.get("extract") or ""
        if not extract:
            return "empty page"
        if len(extract) > 1800:
            extract = extract[:1800] + "..."
        return f"{data.get('title', title)}\n{extract}"
    except Exception as e:
        return f"read failed: {e}"


def _dump_msg(msg) -> dict:
    d = {"role": msg.role, "content": msg.content or ""}
    if msg.tool_calls:
        d["tool_calls"] = []
        for tc in msg.tool_calls:
            d["tool_calls"].append(
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
            )
    return d


@observe(
    type="llm",
    prompt=DIG_PROMPT,
    expectations=[Expectation("constraint", "names the Wikipedia page title in each bullet")],
)
def llm(messages: list[dict], tools=None) -> object:
    kwargs = {"model": MODEL, "messages": messages}
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    print(f"\n--- llm call ({len(messages)} msgs, tools={bool(tools)}) ---", flush=True)
    resp = client.chat.completions.create(**kwargs)
    msg = resp.choices[0].message
    if msg.tool_calls:
        names = [tc.function.name for tc in msg.tool_calls]
        print(f"tool_calls: {names}", flush=True)
    else:
        preview = (msg.content or "").replace("\n", " ")[:160]
        print(f"text: {preview}", flush=True)
    return msg


def _parse_queries(text: str | None) -> list[str]:
    raw = (text or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()
    start = raw.find("[")
    end = raw.rfind("]")
    if start != -1 and end != -1:
        raw = raw[start : end + 1]
    queries = json.loads(raw)
    out = []
    for q in queries:
        if isinstance(q, str) and q.strip():
            out.append(q.strip())
    return out[:2]


def _run_tool(name: str, arguments: str) -> str:
    try:
        args = json.loads(arguments or "{}")
    except Exception:
        args = {}
    if name == "wiki_search":
        print(f"  wiki_search({args.get('query')!r})", flush=True)
        found = wiki_search(args.get("query") or "")
        return found + "\n\nStop searching. Call wiki_read on the best title."
    if name == "wiki_read":
        print(f"  wiki_read({args.get('title')!r})", flush=True)
        return wiki_read(args.get("title") or "")
    return f"unknown tool {name}"


@observe(type="workflow")
def dig(query: str) -> str:
    """Research one Wikipedia query into factual bullets."""
    with task("dig-query", unit="turn"):
        messages = [
            {"role": "system", "content": DIG_PROMPT},
            {"role": "user", "content": query},
        ]
        for _ in range(4):
            msg = llm(messages, TOOLS)
            messages.append(_dump_msg(msg))
            if not msg.tool_calls:
                return msg.content or "(no notes)"
            for tc in msg.tool_calls:
                result = _run_tool(tc.function.name, tc.function.arguments)
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result[:4000],
                    }
                )
        messages.append(
            {
                "role": "user",
                "content": "Stop searching. Write the bullets from what you already have.",
            }
        )
        msg = llm(messages)
        return msg.content or "(no notes)"


@capability("research", description="Answer a research question from Wikipedia evidence")
def research(question: str) -> str:
    """Plan Wikipedia queries, dig notes, and write a short brief."""
    with task("research-run", unit="turn"):
        plan = llm(
            [
                {"role": "system", "content": RESEARCH_PROMPT},
                {"role": "user", "content": question},
            ]
        )
        try:
            queries = _parse_queries(plan.content)
        except Exception as e:
            print(f"plan parse failed ({e}), using the question itself", flush=True)
            queries = [question]
        if not queries:
            queries = [question]
        print(f"queries: {queries}", flush=True)

        notes = []
        for q in queries:
            print(f"\n==== dig: {q} ====", flush=True)
            notes.append(f"QUERY: {q}\n{dig(q)}")

        blob = "\n\n".join(notes)
        report = llm(
            [
                {"role": "system", "content": BRIEF_PROMPT},
                {"role": "user", "content": f"Question: {question}\n\nNotes:\n{blob}"},
            ]
        )
        return report.content or ""


if __name__ == "__main__":
    overmind.init(
        service_name=os.environ.get("OVERMIND_SERVICE_NAME") or "kimm",
        capability="research",
    )
    question = " ".join(sys.argv[1:]).strip()
    if not question:
        question = (
            "How did the James Webb Space Telescope change estimates of "
            "when the first galaxies formed?"
        )
    print(research(question))
    overmind.force_flush_traces()
