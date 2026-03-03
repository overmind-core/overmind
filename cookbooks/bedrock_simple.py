"""
AWS Bedrock Simple — Overmind OTEL Cookbook
============================================
Traces AWS Bedrock model invocations (via boto3) and sends spans to Overmind.
Uses Claude 3.5 Haiku on Bedrock as the example model; swap `model_id` for
any Bedrock-supported model.

Install:
    pip install boto3 \
                opentelemetry-sdk \
                opentelemetry-exporter-otlp-proto-http \
                opentelemetry-instrumentation-bedrock

AWS credentials required:
    AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_DEFAULT_REGION

Run:
    OVERMIND_API_KEY=ovr_... python cookbooks/bedrock_simple.py
"""

import json
import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.bedrock import BedrockInstrumentor
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

# ---------------------------------------------------------------------------
# Overmind OTEL setup
# ---------------------------------------------------------------------------

OVERMIND_ENDPOINT = "https://api.overmindlab.ai/api/v1/traces/create"

resource = Resource.create(
    {
        "service.name": "bedrock-simple",
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
BedrockInstrumentor().instrument(tracer_provider=provider)

# ---------------------------------------------------------------------------
# Application code
# ---------------------------------------------------------------------------

import boto3  # noqa: E402

bedrock = boto3.client(
    "bedrock-runtime",
    region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
)

MODEL_ID = "anthropic.claude-3-5-haiku-20241022-v1:0"


def invoke_claude(prompt: str, max_tokens: int = 512) -> str:
    body = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "messages": [
            {"role": "user", "content": prompt},
        ],
    }
    response = bedrock.invoke_model(
        modelId=MODEL_ID,
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )
    result = json.loads(response["body"].read())
    return result["content"][0]["text"]


def invoke_titan(prompt: str) -> str:
    """Alternative: Amazon Titan Text — swap model_id to use this instead."""
    body = {
        "inputText": prompt,
        "textGenerationConfig": {
            "maxTokenCount": 512,
            "temperature": 0.7,
        },
    }
    response = bedrock.invoke_model(
        modelId="amazon.titan-text-lite-v1",
        contentType="application/json",
        accept="application/json",
        body=json.dumps(body),
    )
    result = json.loads(response["body"].read())
    return result["results"][0]["outputText"]


if __name__ == "__main__":
    answer = invoke_claude("Explain OpenTelemetry in one sentence.")
    print("Claude on Bedrock:", answer)

    provider.force_flush()
