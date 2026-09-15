from __future__ import annotations

import os
import sys

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "overbae.settings")
django.setup()

from overbae.core.llms import ToolStreamResult, stream_llm_tools  # noqa: E402
from overbae.core.model_registry import WORKSHOP_ENGINES  # noqa: E402

TOOL = {
    "type": "function",
    "function": {
        "name": "count_rows",
        "description": "Count the rows of the table.",
        "parameters": {
            "type": "object",
            "properties": {"table": {"type": "string"}},
            "required": ["table"],
        },
    },
}
MESSAGES = [
    {
        "role": "system",
        "content": [
            {
                "type": "text",
                "text": "You are a terse assistant. Use the tool when a count is asked for.",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    },
    {"role": "user", "content": "How many rows does table t have? Call the tool."},
]


def main() -> int:
    failures = 0
    for engine in WORKSHOP_ENGINES:
        provider = engine.provider
        if provider.name == "cursor" or not provider.configured():
            continue
        print(f"== {provider.name} · {engine.model}")
        try:
            for item in stream_llm_tools(
                MESSAGES,
                [TOOL],
                model=engine.model,
                fallback_models=list(engine.models),
                max_tokens=300,
                reasoning_effort="low",
                provider=provider,
            ):
                if isinstance(item, ToolStreamResult):
                    calls = [
                        (c["function"]["name"], c["function"]["arguments"]) for c in item.tool_calls
                    ]
                    print(f"   text={item.text!r} calls={calls}")
                    print(f"   reasoning={len(item.reasoning)} chars stats={item.stats}")
                    if not calls:
                        failures += 1
                        print("   FAIL: no tool call streamed")
                else:
                    sys.stdout.write("." if item.kind == "text" else "r")
                    sys.stdout.flush()
            print()
        except Exception as exc:  # noqa: BLE001 — the point is to see it
            failures += 1
            print(f"   FAIL: {type(exc).__name__}: {exc}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
