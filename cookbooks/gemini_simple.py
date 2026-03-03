"""
Google Gemini Simple — Overmind OTEL Cookbook
=============================================
Traces Google Gemini API calls and sends spans to Overmind.

Install:
    pip install google-generativeai \
                opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-http \
                opentelemetry-instrumentation-google-generativeai

Run:
    OVERMIND_API_KEY=ovr_... GOOGLE_API_KEY=... python cookbooks/gemini_simple.py
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.google_generativeai import GoogleGenerativeAiInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# ---------------------------------------------------------------------------
# Overmind OTEL setup
# ---------------------------------------------------------------------------

OVERMIND_ENDPOINT = "https://api.overmindlab.ai/api/v1/traces/create"

resource = Resource.create(
    {
        "service.name": "gemini-simple",
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
GoogleGenerativeAiInstrumentor().instrument(tracer_provider=provider)

# ---------------------------------------------------------------------------
# Application code
# ---------------------------------------------------------------------------

import google.generativeai as genai  # noqa: E402

genai.configure(api_key=os.environ["GOOGLE_API_KEY"])


def ask(prompt: str, model_name: str = "gemini-2.0-flash") -> str:
    model = genai.GenerativeModel(model_name)
    response = model.generate_content(prompt)
    return response.text


def chat_session(model_name: str = "gemini-2.0-flash") -> None:
    """Multi-turn conversation using Gemini's chat interface."""
    model = genai.GenerativeModel(model_name)
    chat = model.start_chat()

    turns = [
        "What is OpenTelemetry?",
        "How does it help with LLM observability specifically?",
        "Give me a one-line summary of everything you just said.",
    ]

    for user_message in turns:
        print(f"\nUser: {user_message}")
        response = chat.send_message(user_message)
        print(f"Gemini: {response.text}")


if __name__ == "__main__":
    # Single-turn
    answer = ask("Explain OpenTelemetry in one sentence.")
    print("Single turn:", answer)

    # Multi-turn chat
    print("\n--- Multi-turn chat ---")
    chat_session()

    provider.force_flush()
