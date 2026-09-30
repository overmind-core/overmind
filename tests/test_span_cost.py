"""An ingested LLM span carries one cost: reported by the producer, else priced
from its model and tokens at ingest. The span, the trace totals and the
capability rollup all read that one value.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest
from opentelemetry.proto.common.v1.common_pb2 import AnyValue, KeyValue
from opentelemetry.proto.trace.v1.trace_pb2 import ResourceSpans, ScopeSpans
from opentelemetry.proto.trace.v1.trace_pb2 import Span as OtlpSpan
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from overbae.models import Capability, Project, ProjectMembership, User

pytestmark = pytest.mark.django_db

# Prices are looked up at the span's start time, so a fixed date keeps them stable.
_START_NS = int(datetime(2026, 1, 1, tzinfo=UTC).timestamp() * 1e9)


@pytest.fixture
def ingest():
    user = User.objects.create_user(
        email=f"u-{uuid.uuid4().hex[:6]}@example.com",
        password="test-pass-123",
        clerk_user_id=f"clerk_{uuid.uuid4().hex}",
    )
    project = Project.objects.create(name="P", slug=f"p-{uuid.uuid4().hex[:8]}")
    ProjectMembership.objects.create(user=user, project=project)
    capability = Capability.objects.create(project=project, name="Triage", slug="triage")
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {RefreshToken.for_user(user).access_token}")

    def post(attributes: dict) -> dict:
        trace_id = uuid.uuid4().bytes
        request = ExportTraceServiceRequest(
            resource_spans=[
                ResourceSpans(
                    resource={"attributes": _kv({"overmind.capability.id": str(capability.id)})},
                    scope_spans=[
                        ScopeSpans(
                            spans=[
                                OtlpSpan(
                                    trace_id=trace_id,
                                    span_id=uuid.uuid4().bytes[:8],
                                    name="chat",
                                    start_time_unix_nano=_START_NS,
                                    end_time_unix_nano=_START_NS + 1_000_000,
                                    attributes=_kv(attributes),
                                )
                            ]
                        )
                    ],
                )
            ]
        )
        res = client.post(
            "/api/v1/traces",
            request.SerializeToString(),
            content_type="application/x-protobuf",
        )
        assert res.status_code == 200, res.content
        detail = client.get(f"/api/traces/{trace_id.hex()}/").json()
        listed = client.get("/api/traces/").json()["results"]
        capability.refresh_from_db()
        return {
            "span": detail["spans"][0]["attributes"].get("genai.cost"),
            "trace": detail["usage"]["total_cost"],
            "list": next(r for r in listed if r["trace_id"] == trace_id.hex())["total_cost"],
            "capability": (capability.usage_stats or {}).get("cost_usd"),
        }

    return post


def _kv(attributes: dict) -> list[KeyValue]:
    def any_value(v):
        if isinstance(v, str):
            return AnyValue(string_value=v)
        if isinstance(v, float):
            return AnyValue(double_value=v)
        return AnyValue(int_value=v)

    return [KeyValue(key=k, value=any_value(v)) for k, v in attributes.items()]


def test_unreported_cost_is_priced_once_for_every_reader(ingest):
    costs = ingest(
        {
            "gen_ai.system": "openai",
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.input_tokens": 1000,
            "gen_ai.usage.cache_read.input_tokens": 200,
            "gen_ai.usage.output_tokens": 500,
        }
    )
    # gpt-4o: 800 fresh x $2.50/M + 200 cached x $1.25/M + 500 out x $10/M
    assert costs == {k: pytest.approx(0.00725) for k in costs}


@pytest.mark.parametrize(
    "attributes, expected",
    [
        pytest.param(
            {
                "gen_ai.request.model": "openai/gpt-4o",
                "gen_ai.usage.input_tokens": 1000,
                "gen_ai.usage.output_tokens": 500,
            },
            0.0075,
            id="routed-slug",
        ),
        pytest.param(
            {
                "gen_ai.system": "anthropic",
                "gen_ai.request.model": "claude-sonnet-4-5",
                "gen_ai.usage.input_tokens": 100,
                "gen_ai.usage.cache_read_input_tokens": 1000,
                "gen_ai.usage.output_tokens": 50,
            },
            # 100 fresh x $3/M + 1000 cached x $0.30/M + 50 out x $15/M
            0.00135,
            id="cache-reads-outside-input",
        ),
    ],
)
def test_usage_shapes_price_at_list_price(ingest, attributes, expected):
    costs = ingest(attributes)
    assert costs == {k: pytest.approx(expected) for k in costs}


def test_reported_cost_is_kept(ingest):
    costs = ingest(
        {
            "gen_ai.request.model": "gpt-4o",
            "gen_ai.usage.input_tokens": 1000,
            "gen_ai.usage.output_tokens": 500,
            "gen_ai.usage.cost": 0.42,
        }
    )
    assert costs == {k: pytest.approx(0.42) for k in costs}


def test_unpriced_model_stays_without_cost(ingest):
    costs = ingest(
        {
            "gen_ai.request.model": "overmind/ft-support-copilot",
            "gen_ai.usage.input_tokens": 1000,
            "gen_ai.usage.output_tokens": 500,
        }
    )
    assert costs == {"span": None, "trace": None, "list": None, "capability": None}
