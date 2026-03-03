# Overmind Cookbooks

Examples showing how to use Overmind as an OpenTelemetry (OTEL) observability platform for LLM applications. Every cookbook instruments its LLM framework with the OTLP exporter pointed at Overmind, so all traces appear in your Overmind dashboard automatically.

## How it works

All traces are sent to:

```
https://api.overmindlab.ai/api/v1/traces/create
```

with the header `X-API-Token: $OVERMIND_API_KEY`. Each cookbook sets up the OpenTelemetry provider and attaches the appropriate instrumentor for its framework — no changes to your existing LLM code are required.

## Setup

```bash
export OVERMIND_API_KEY=ovr_...
```

Install shared OTEL dependencies (all cookbooks require these):

```bash
pip install opentelemetry-sdk opentelemetry-exporter-otlp-proto-http
```

## Cookbooks

| File | Framework | Extra dependencies |
|------|-----------|-------------------|
| `openai_simple.py` | OpenAI Chat Completions | `openai opentelemetry-instrumentation-openai` |
| `openai_agents.py` | OpenAI Agents SDK | `openai openai-agents opentelemetry-instrumentation-openai` |
| `anthropic_simple.py` | Anthropic Messages API | `anthropic opentelemetry-instrumentation-anthropic` |
| `bedrock_simple.py` | AWS Bedrock (boto3) | `boto3 opentelemetry-instrumentation-bedrock` |
| `gemini_simple.py` | Google Gemini | `google-generativeai opentelemetry-instrumentation-google-generativeai` |
| `crewai_agent.py` | CrewAI multi-agent | `crewai crewai-tools opentelemetry-instrumentation-crewai opentelemetry-instrumentation-openai` |
| `agno_agent.py` | Agno AI multi-agent | `agno opentelemetry-instrumentation-openai` |

## Running a cookbook

```bash
# Example: run the OpenAI simple cookbook
pip install openai opentelemetry-sdk opentelemetry-exporter-otlp-proto-http opentelemetry-instrumentation-openai
export OVERMIND_API_KEY=ovr_...
python cookbooks/openai_simple.py
```

Open your Overmind dashboard to see the trace appear within a few seconds.
