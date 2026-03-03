"""
CrewAI Multi-Agent — Overmind OTEL Cookbook
============================================
Traces a two-agent CrewAI pipeline (researcher + writer) and sends all spans
to Overmind. Both the CrewAI orchestration and the underlying OpenAI LLM calls
are captured in the same trace.

Install:
    pip install crewai \
                crewai-tools \
                opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-http \
                opentelemetry-instrumentation-crewai \
                opentelemetry-instrumentation-openai

Run:
    OVERMIND_API_KEY=ovr_... OPENAI_API_KEY=sk-... python cookbooks/crewai_agent.py
"""

import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.crewai import CrewAIInstrumentor
from opentelemetry.instrumentation.openai import OpenAIInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# ---------------------------------------------------------------------------
# Overmind OTEL setup — must happen before crewai/openai are imported
# ---------------------------------------------------------------------------

OVERMIND_ENDPOINT = "https://api.overmindlab.ai/api/v1/traces/create"

resource = Resource.create(
    {
        "service.name": "crewai-agent",
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
CrewAIInstrumentor().instrument(tracer_provider=provider)
OpenAIInstrumentor().instrument(tracer_provider=provider)

# ---------------------------------------------------------------------------
# CrewAI agent definitions
# ---------------------------------------------------------------------------

from crewai import Agent, Crew, Process, Task  # noqa: E402

researcher = Agent(
    role="Senior Research Analyst",
    goal="Uncover the latest trends in LLM observability and AI infrastructure tooling",
    backstory=(
        "You are an expert analyst at a leading tech research firm. "
        "You excel at identifying emerging patterns in the AI tooling ecosystem and "
        "synthesising actionable insights from disparate sources."
    ),
    verbose=True,
)

writer = Agent(
    role="Tech Content Strategist",
    goal="Transform technical research into clear, engaging content for developers",
    backstory=(
        "You are a seasoned technology writer with a strong engineering background. "
        "You have a gift for making complex distributed systems concepts accessible "
        "without sacrificing depth or accuracy."
    ),
    verbose=True,
)

# ---------------------------------------------------------------------------
# Task definitions
# ---------------------------------------------------------------------------

research_task = Task(
    description=(
        "Conduct a comprehensive analysis of the current state of LLM observability platforms. "
        "Cover: key players (open-source and commercial), OpenTelemetry adoption patterns, "
        "tracing and evaluation capabilities, and what production teams care about most."
    ),
    expected_output=(
        "A structured bullet-point analysis with: key findings, notable tools, "
        "adoption trends, and gaps in the current ecosystem."
    ),
    agent=researcher,
)

writing_task = Task(
    description=(
        "Using the research findings, write an engaging blog post (4+ paragraphs) "
        "aimed at senior engineers building LLM-powered products in production. "
        "The post should explain why observability matters for LLMs specifically "
        "and what to look for in an observability platform."
    ),
    expected_output=(
        "A complete, publication-ready blog post with a compelling introduction, "
        "body paragraphs covering key insights, and a concise conclusion."
    ),
    agent=writer,
)

# ---------------------------------------------------------------------------
# Crew assembly and execution
# ---------------------------------------------------------------------------

crew = Crew(
    agents=[researcher, writer],
    tasks=[research_task, writing_task],
    process=Process.sequential,
    verbose=True,
)

if __name__ == "__main__":
    result = crew.kickoff()
    print("\n" + "=" * 60)
    print("Final output:")
    print("=" * 60)
    print(result)

    provider.force_flush()
