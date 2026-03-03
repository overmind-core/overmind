"""
OpenAI Simple — Overmind OTEL Cookbook
=======================================
Traces a basic OpenAI chat completion and sends the spans to Overmind.

Install:
    pip install openai \
                opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-http \
                opentelemetry-instrumentation-openai

Run:
    OVERMIND_API_KEY=ovr_... OPENAI_API_KEY=sk-... python cookbooks/openai_simple.py
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# ---------------------------------------------------------------------------
# Overmind OTEL setup
# ---------------------------------------------------------------------------

OVERMIND_ENDPOINT = "https://api.overmindlab.ai/api/v1/traces/create"

resource = Resource.create(
    {
        "service.name": "openai-simple",
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

# Capture message content in spans (prompts + completions)
os.environ["OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT"] = "true"
OpenAIInstrumentor().instrument(tracer_provider=provider)

# ---------------------------------------------------------------------------
# Application code — no changes needed here once tracing is set up above
# ---------------------------------------------------------------------------

from openai import OpenAI  # noqa: E402

client = OpenAI()


def ask(question: str) -> str:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": question},
        ],
    )
    return response.choices[0].message.content


if __name__ == "__main__":
    answer = ask("Explain OpenTelemetry in one sentence.")
    print(answer)

    # Ensure all spans are exported before the process exits
    provider.force_flush()
