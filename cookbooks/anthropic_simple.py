"""
Anthropic Simple — Overmind OTEL Cookbook
=========================================
Traces an Anthropic Messages API call and sends the span to Overmind.

Install:
    pip install anthropic \
                opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-http \
                opentelemetry-instrumentation-anthropic

Run:
    OVERMIND_API_KEY=ovr_... ANTHROPIC_API_KEY=sk-ant-... python cookbooks/anthropic_simple.py
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# ---------------------------------------------------------------------------
# Overmind OTEL setup
# ---------------------------------------------------------------------------

OVERMIND_ENDPOINT = "https://api.overmindlab.ai/api/v1/traces/create"

resource = Resource.create(
    {
        "service.name": "anthropic-simple",
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
    }
)

provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint=OVERMIND_ENDPOINT,
            headers={"X-API-Token": os.environ["OVERMIND_API_KEY"]},
        )
    )
)
trace.set_tracer_provider(provider)

os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "true"
AnthropicInstrumentor().instrument(tracer_provider=provider)

# ---------------------------------------------------------------------------
# Application code
# ---------------------------------------------------------------------------

import anthropic  # noqa: E402

client = anthropic.Anthropic()


def ask(question: str) -> str:
    message = client.messages.create(
        model="claude-3-5-haiku-latest",
        max_tokens=1024,
        messages=[
            {"role": "user", "content": question},
        ],
    )
    return message.content[0].text


def ask_with_system(system: str, question: str) -> str:
    message = client.messages.create(
        model="claude-3-5-haiku-latest",
        max_tokens=1024,
        system=system,
        messages=[
            {"role": "user", "content": question},
        ],
    )
    return message.content[0].text


if __name__ == "__main__":
    # Simple single-turn question
    answer = ask("Explain OpenTelemetry in one sentence.")
    print("Answer:", answer)

    # With a system prompt
    analysis = ask_with_system(
        system="You are a senior software architect who gives concise, opinionated answers.",
        question="Should I use OpenTelemetry for my LLM application in production?",
    )
    print("\nArchitect's take:", analysis)

    provider.force_flush()
