import json
import os
from datetime import datetime
from time import sleep

import requests
from openai import OpenAI
from opentelemetry import trace as otel_trace

from overmind.tracing import init

project_id = os.environ.get("OVERMIND_PROJECT_ID", "e5445c2d-0e4b-4cb1-8a0c-26c18d0ba19f")

base_url = "https://api.overmindlab.ai"
init(service_name="test-spans", environment="local", providers=["openai"])
openai_client = OpenAI()


def check_for_signature(signature: str) -> list[bool]:
    traces_response = requests.get(
        f"{base_url}/api/v1/traces/list",
        params={
            "project_id": project_id,
            "limit": 5,
            "offset": 0,
            "root_only": True,
        },
        headers={
            "X-Api-Key": os.getenv("OVERMIND_API_KEY"),
        },
    )
    assert traces_response.status_code == 200
    traces = traces_response.json()["traces"]
    input_has_signature = []
    for trace in traces:
        has_signature = signature in trace["Inputs"]
        input_has_signature.append(has_signature)
        if not has_signature:
            continue

        span_attributes = trace["SpanAttributes"]
        assert span_attributes["gen_ai.request.model"] == "gpt-5-mini"
        assert span_attributes["gen_ai.completion.0.finish_reason"] == "stop"
        assert span_attributes["gen_ai.request.structured_output_schema"] == json.dumps({"type": "json_object"})

    return input_has_signature


today = str(datetime.now().timestamp())
system_prompt = f"""You are a fashion taxonomy assistant.
Classify the product into exactly one category from the allowed list.
Return strict JSON only in this format:
{{"category": "<one allowed category>", "confidence": <0 to 1>, "reason": "<short reason>"}}
Today's date: {today}"""

user_message = """Allowed categories: Dresses, Tops, Shirts, T-Shirts, Knitwear, Coats, Jackets, Blazers, Jeans, Trousers, Skirts, Shorts, Shoes, Bags, Accessories, Other

Product description:
Triple S Sneaker in light grey microfiber and rhinestones.
Leather free sneaker microfiber and rhinestones complex 3-layered outsole embroidered size at the edge of the toe embroidered logo on the side embossed logo in the back triple s rubber branding on the tongue 2 laces loops including 1 functional lacing system featuring 12 fabric eyelets laces recalling hiking boots' laces back and tongue pull-on tab made in china.
Upper: nylon, polyurethane - Sole: tpu - Insole: foam."""


def test_spans():
    openai_client.chat.completions.create(
        model="gpt-5-mini",
        reasoning_effort="minimal",
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "system",
                "content": system_prompt.strip(),
            },
            {
                "role": "user",
                "content": user_message.strip(),
            },
        ],
    )
    otel_trace.get_tracer_provider().force_flush()

    retries = 4
    for i in range(retries):
        input_has_signature = check_for_signature(today)
        if any(input_has_signature):
            break
        if i < retries - 1:
            sleep(5)

    assert any(input_has_signature), "unable to find trace pushed to prod"
