"""
Agno AI Multi-Agent — Overmind OTEL Cookbook
=============================================
Traces an Agno agent team (research + finance specialists led by a team-lead)
and sends all spans to Overmind. The OpenAI instrumentor captures every LLM
call made by the agents, and a custom parent span groups the full pipeline run.

Install:
    pip install agno \
                duckduckgo-search \
                yfinance \
                opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-http \
                opentelemetry-instrumentation-openai

Run:
    OVERMIND_API_KEY=ovr_... OPENAI_API_KEY=sk-... python cookbooks/agno_agent.py
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
# Overmind OTEL setup — must happen before agno/openai are imported
# ---------------------------------------------------------------------------

OVERMIND_ENDPOINT = "https://api.overmindlab.ai/api/v1/traces/create"

resource = Resource.create(
    {
        "service.name": "agno-agent",
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
# Agno agent definitions
# ---------------------------------------------------------------------------

from agno.agent import Agent  # noqa: E402
from agno.models.openai import OpenAIChat  # noqa: E402
from agno.tools.duckduckgo import DuckDuckGoTools  # noqa: E402
from agno.tools.yfinance import YFinanceTools  # noqa: E402

tracer = trace.get_tracer("agno-cookbook")

research_agent = Agent(
    name="ResearchAgent",
    role="Web research specialist",
    model=OpenAIChat(id="gpt-4o-mini"),
    tools=[DuckDuckGoTools()],
    instructions=[
        "Search the web for up-to-date information on the topic.",
        "Always cite the source URLs for key claims.",
        "Prioritise recent news (last 3 months).",
    ],
    show_tool_calls=True,
    markdown=True,
)

finance_agent = Agent(
    name="FinanceAgent",
    role="Financial data analyst",
    model=OpenAIChat(id="gpt-4o-mini"),
    tools=[
        YFinanceTools(
            stock_price=True,
            analyst_recommendations=True,
            company_info=True,
        )
    ],
    instructions=[
        "Provide data-driven financial analysis.",
        "Use tables for numerical comparisons.",
        "Highlight analyst consensus and any significant divergence.",
    ],
    show_tool_calls=True,
    markdown=True,
)

team_lead = Agent(
    name="TeamLead",
    role="Synthesises research and finance insights into a unified executive report",
    model=OpenAIChat(id="gpt-4o-mini"),
    team=[research_agent, finance_agent],
    instructions=[
        "Coordinate the research and finance agents to gather comprehensive data.",
        "Produce a concise executive summary (headline + 3 key bullet points).",
        "Follow with a deeper analysis section covering news, financials, and outlook.",
    ],
    show_tool_calls=True,
    markdown=True,
)

# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


async def run_pipeline(query: str) -> None:
    with tracer.start_as_current_span("agno-multi-agent-pipeline") as span:
        span.set_attribute("query", query)
        span.set_attribute("agents", "ResearchAgent, FinanceAgent, TeamLead")
        await team_lead.arun(query)


if __name__ == "__main__":
    query = (
        "Give me an overview of the top AI infrastructure companies: "
        "current news, stock performance, and analyst sentiment."
    )
    asyncio.run(run_pipeline(query))

    provider.force_flush()
