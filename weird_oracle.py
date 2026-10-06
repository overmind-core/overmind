# A slightly unhinged sock-bureaucracy agent, observed by Overmind.
#   uv run --directory overmind python ../weird_oracle.py "where is my left sock"

from __future__ import annotations

import json
import os
import random
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

import overmind
from overmind import Expectation, capability, observe, task

_here = Path(__file__).resolve().parent
load_dotenv(_here / ".env", override=True)

MODEL = "openai/gpt-5-mini"
client = OpenAI(base_url="https://openrouter.ai/api/v1", api_key=os.environ["OPENROUTER_API_KEY"])

ORACLE_PROMPT = (
    "You are the Chief Sock Arbitrator for a household that lost one sock. "
    "Speak like a tired civil servant who has seen too many lint rollers. "
    "Return a short verdict in plain prose under 80 words."
)

# The dryer is a known liar. The cat is a known thief. The laundry basket is silent.
SPIRITS = ("dryer-goblin", "cat-with-a-past", "basket-of-unspoken-things")


@observe(type="tool")
def shake_the_dryer(query: str) -> dict:
    """Rattle the dryer and invent what falls out."""
    finds = [
        "a single button from a shirt that no longer exists",
        "three softballs of grey lint shaped like continents",
        "a receipt from 2019 for socks that promised immortality",
        "nothing, but the drum hums in C minor",
    ]
    return {"spirit": "dryer-goblin", "query": query, "find": random.choice(finds)}


@observe(type="tool")
def interrogate_the_cat(query: str) -> dict:
    """Ask the cat. The cat answers in blinks."""
    blinks = random.randint(1, 5)
    meaning = {
        1: "denial",
        2: "guilty curiosity",
        3: "sovereign indifference",
        4: "it knows and will never tell",
        5: "the sock is under the sofa, but pride forbids saying so",
    }[blinks]
    return {"spirit": "cat-with-a-past", "query": query, "blinks": blinks, "meaning": meaning}


@observe(type="retrieval")
def consult_laundry_precedent(query: str) -> list[str]:
    """Retrieve case law from the laundry tribunal."""
    return [
        f"In re Missing Cufflink (2017): proximity to dryer is not guilt — query={query!r}",
        "Household v. Gravity (2021): socks migrate toward entropy, not malice",
        "The People of the Bathroom Mat (2023): one of a pair is already half a goodbye",
    ]


@observe(
    type="llm",
    prompt=ORACLE_PROMPT,
    expectations=[
        Expectation("constraint", "mentions at least one spirit by name"),
        Expectation("constraint", "does not invent a second missing sock"),
    ],
)
def write_verdict(question: str, evidence: list[dict]) -> str:
    """Turn spirit evidence into a bureaucratic sock verdict."""
    messages = [
        {"role": "system", "content": ORACLE_PROMPT},
        {
            "role": "user",
            "content": (
                f"Question: {question}\n\n"
                f"Evidence from the spirits:\n{json.dumps(evidence, indent=2)}\n\n"
                "Issue a verdict. Name the responsible spirit if any."
            ),
        },
    ]
    print("--- consulting the tribunal ---", flush=True)
    resp = client.chat.completions.create(model=MODEL, messages=messages)
    text = (resp.choices[0].message.content or "").strip()
    print(text, flush=True)
    return text


@observe(type="workflow")
def convene_spirits(question: str) -> list[dict]:
    """Shake every spirit until someone confesses or the lint wins."""
    with task("spirit-roundtable", unit="turn"):
        evidence = [
            shake_the_dryer(question),
            interrogate_the_cat(question),
            {
                "spirit": "basket-of-unspoken-things",
                "precedent": consult_laundry_precedent(question),
            },
        ]
        # The basket refuses to speak unless the other two disagree.
        if evidence[0]["find"] == evidence[1]["meaning"]:
            evidence.append({"spirit": "lint", "note": "they agree; something is wrong"})
        return evidence


@capability(
    "sock-oracle",
    description="Locate a missing sock via dryer, cat, and laundry case law",
)
def find_the_sock(question: str) -> str:
    """End-to-end sock arbitration for one household crisis."""
    with task("sock-case", unit="turn"):
        evidence = convene_spirits(question)
        return write_verdict(question, evidence)


if __name__ == "__main__":
    # No project API key handy (local API is restarting). Observe in-process:
    # stand up a TracerProvider, then let overmind.init() reuse it via
    # OVERMIND_TRACE_FILE.
    from opentelemetry import trace as otel_trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    trace_file = Path(os.environ.get("OVERMIND_TRACE_FILE") or "/tmp/weird-oracle-spans.jsonl")
    os.environ.setdefault("OVERMIND_TRACE_FILE", str(trace_file))
    memory = InMemorySpanExporter()
    provider = TracerProvider(resource=Resource.create({"service.name": "weird-oracle"}))
    provider.add_span_processor(SimpleSpanProcessor(memory))
    otel_trace.set_tracer_provider(provider)

    overmind.init(service_name="weird-oracle", capability="sock-oracle")
    question = (
        " ".join(sys.argv[1:]).strip() or "Where did my left navy sock go after Tuesday's wash?"
    )
    print(f"case file: {question}", flush=True)
    verdict = find_the_sock(question)
    print("\n=== FINAL VERDICT ===", flush=True)
    print(verdict)
    overmind.force_flush_traces()

    rows = []
    for span in memory.get_finished_spans():
        a = dict(span.attributes or {})
        rows.append(
            {
                "name": span.name,
                "type": a.get("overmind.span.type") or a.get("overmind.span_type"),
                "capability.slug": a.get("overmind.capability.slug"),
                "behaviour.key": a.get("overmind.behaviour.key"),
                "unit_kind": a.get("overmind.unit_kind"),
            }
        )
    trace_file.write_text("\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""))
    print(f"\n=== OVERMIND OBSERVATION ({len(rows)} spans) → {trace_file} ===", flush=True)
    for r in rows:
        print(
            f"  {str(r['type'] or '?'):12} {r['name'][:46]:46} "
            f"slug={r['capability.slug'] or '-'} "
            f"key={r['behaviour.key'] or '-'} "
            f"unit={r['unit_kind'] or '-'}",
            flush=True,
        )
