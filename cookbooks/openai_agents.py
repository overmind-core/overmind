"""
OpenAI Agents SDK — Overmind OTEL Cookbook
==========================================
Traces a two-agent pipeline (researcher → summariser) built with the
OpenAI Agents SDK and sends all spans to Overmind.

Install:
    pip install openai \
                openai-agents \
                opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-http \
                opentelemetry-instrumentation-openai

Run:
    OVERMIND_API_KEY=ovr_... OPENAI_API_KEY=sk-... python cookbooks/openai_agents.py
"""

import asyncio
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
        "service.name": "openai-agents",
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
OpenAIInstrumentor().instrument(tracer_provider=provider)

# ---------------------------------------------------------------------------
# Agent definitions
# ---------------------------------------------------------------------------

from agents import Agent, Runner  # noqa: E402

research_agent = Agent(
    name="ResearchAgent",
    instructions=(
        "You are a research assistant. Answer questions concisely with well-sourced facts. "
        "Structure your response with clear sections."
    ),
    model="gpt-4o-mini",
)

summary_agent = Agent(
    name="SummaryAgent",
    instructions=(
        "You are an expert summariser. Condense the input into 3 concise bullet points, "
        "each starting with a bold key term."
    ),
    model="gpt-4o-mini",
)

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

tracer = trace.get_tracer("openai-agents-cookbook")


async def run_pipeline(topic: str) -> str:
    with tracer.start_as_current_span("research-summarise-pipeline") as span:
        span.set_attribute("pipeline.topic", topic)

        research_result = await Runner.run(research_agent, topic)
        research_output = research_result.final_output
        span.set_attribute("research.char_count", len(research_output))

        summary_result = await Runner.run(
            summary_agent,
            f"Summarise the following research:\n\n{research_output}",
        )
        summary = summary_result.final_output
        span.set_attribute("summary.char_count", len(summary))

    return summary


if __name__ == "__main__":
    topic = "What are the main benefits of OpenTelemetry for LLM applications?"
    result = asyncio.run(run_pipeline(topic))
    print(result)

    provider.force_flush()
