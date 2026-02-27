"""
Tests for raw OTLP request storage and error handling.

Verifies that the raw protobuf body is always persisted in raw_otlp_requests,
both on successful ingestion and when transformation fails.
"""

import time
import zlib
from unittest.mock import patch
from uuid import uuid4

import pytest
from opentelemetry.proto.collector.trace.v1 import trace_service_pb2
from opentelemetry.proto.trace.v1 import trace_pb2
from opentelemetry.proto.common.v1 import common_pb2
from opentelemetry.proto.resource.v1 import resource_pb2
from sqlalchemy import select

from overmind.models.traces import RawOtlpRequestModel


def _build_export_request(
    trace_id: bytes | None = None,
    span_id: bytes | None = None,
    span_name: str = "llm.chat",
    project_id: str | None = None,
    user_id: str | None = None,
) -> trace_service_pb2.ExportTraceServiceRequest:
    """Build a minimal valid ExportTraceServiceRequest protobuf."""
    trace_id = trace_id or uuid4().bytes
    span_id = span_id or uuid4().bytes[:8]
    now_ns = int(time.time() * 1_000_000_000)

    attributes = []
    if project_id:
        attributes.append(
            common_pb2.KeyValue(
                key="project_id",
                value=common_pb2.AnyValue(string_value=project_id),
            )
        )
    if user_id:
        attributes.append(
            common_pb2.KeyValue(
                key="user_id",
                value=common_pb2.AnyValue(string_value=user_id),
            )
        )

    span = trace_pb2.Span(
        trace_id=trace_id,
        span_id=span_id,
        name=span_name,
        start_time_unix_nano=now_ns,
        end_time_unix_nano=now_ns + 1_000_000,
        attributes=attributes,
    )
    scope_spans = trace_pb2.ScopeSpans(spans=[span])
    resource_spans = trace_pb2.ResourceSpans(
        resource=resource_pb2.Resource(),
        scope_spans=[scope_spans],
    )
    return trace_service_pb2.ExportTraceServiceRequest(resource_spans=[resource_spans])


async def _post_otlp(test_client, api_token_headers, body: bytes, gzip: bool = False):
    """POST raw protobuf bytes to the OTLP create endpoint."""
    headers = {**api_token_headers, "Content-Type": "application/x-protobuf"}
    if gzip:
        body = zlib.compress(body, wbits=16 + zlib.MAX_WBITS)
        headers["content-encoding"] = "gzip"
    return await test_client.post(
        "/api/v1/traces/create", content=body, headers=headers
    )


async def _get_raw_records(db_session) -> list[RawOtlpRequestModel]:
    result = await db_session.execute(select(RawOtlpRequestModel))
    return list(result.scalars().all())


class TestRawOtlpStorageHappyPath:
    async def test_raw_record_created_on_successful_ingest(
        self, seed_user, test_client, api_token_headers, db_session
    ):
        export_req = _build_export_request(
            project_id=str(seed_user.project.project_id),
            user_id=str(seed_user.user.user_id),
        )
        body = export_req.SerializeToString()
        resp = await _post_otlp(test_client, api_token_headers, body)

        assert resp.status_code == 200
        assert "Successfully processed" in resp.json()["message"]

        records = await _get_raw_records(db_session)
        assert len(records) == 1
        raw = records[0]
        assert raw.raw_body == body
        assert raw.body_size == len(body)
        assert raw.error is None
        assert raw.content_encoding is None

    async def test_trace_ids_populated_on_success(
        self, seed_user, test_client, api_token_headers, db_session
    ):
        trace_id = uuid4().bytes
        export_req = _build_export_request(
            trace_id=trace_id,
            project_id=str(seed_user.project.project_id),
            user_id=str(seed_user.user.user_id),
        )
        resp = await _post_otlp(
            test_client, api_token_headers, export_req.SerializeToString()
        )
        assert resp.status_code == 200

        records = await _get_raw_records(db_session)
        assert len(records) == 1
        assert trace_id.hex() in records[0].trace_ids

    async def test_gzip_body_stored_decompressed(
        self, seed_user, test_client, api_token_headers, db_session
    ):
        export_req = _build_export_request(
            project_id=str(seed_user.project.project_id),
            user_id=str(seed_user.user.user_id),
        )
        raw_body = export_req.SerializeToString()
        resp = await _post_otlp(test_client, api_token_headers, raw_body, gzip=True)
        assert resp.status_code == 200

        records = await _get_raw_records(db_session)
        assert len(records) == 1
        assert records[0].raw_body == raw_body
        assert records[0].content_encoding == "gzip"

    async def test_empty_request_still_creates_raw_record(
        self, seed_user, test_client, api_token_headers, db_session
    ):
        empty_req = trace_service_pb2.ExportTraceServiceRequest()
        body = empty_req.SerializeToString()
        resp = await _post_otlp(test_client, api_token_headers, body)

        assert resp.status_code == 200
        assert "Empty request" in resp.json()["message"]

        records = await _get_raw_records(db_session)
        assert len(records) == 1
        assert records[0].error is None
        assert records[0].trace_ids == []


class TestRawOtlpStorageErrorHandling:
    """Error handling tests.

    The ASGI test transport re-raises unhandled app exceptions, so we use
    pytest.raises to catch the exception that create_trace re-raises after
    saving the raw record.
    """

    async def test_raw_record_saved_when_transform_fails(
        self, seed_user, test_client, api_token_headers, db_session
    ):
        export_req = _build_export_request(
            project_id=str(seed_user.project.project_id),
            user_id=str(seed_user.user.user_id),
        )
        body = export_req.SerializeToString()

        with (
            patch(
                "overmind.api.v1.endpoints.otlp.transformers.transform_spans",
                side_effect=ValueError("test explosion"),
            ),
            pytest.raises(ValueError, match="test explosion"),
        ):
            await _post_otlp(test_client, api_token_headers, body)

        records = await _get_raw_records(db_session)
        assert len(records) == 1
        raw = records[0]
        assert raw.raw_body == body
        assert raw.body_size == len(body)
        assert raw.error is not None
        assert "ValueError" in raw.error
        assert "test explosion" in raw.error
        assert raw.trace_ids == []

    async def test_raw_record_saved_when_postgres_insert_fails(
        self, seed_user, test_client, api_token_headers, db_session
    ):
        export_req = _build_export_request(
            project_id=str(seed_user.project.project_id),
            user_id=str(seed_user.user.user_id),
        )
        body = export_req.SerializeToString()

        with (
            patch(
                "overmind.api.v1.endpoints.otlp.transformers.tranform_spans_for_postgres",
                side_effect=RuntimeError("db insert kaboom"),
            ),
            pytest.raises(RuntimeError, match="db insert kaboom"),
        ):
            await _post_otlp(test_client, api_token_headers, body)

        records = await _get_raw_records(db_session)
        assert len(records) == 1
        raw = records[0]
        assert raw.raw_body == body
        assert "RuntimeError" in raw.error
        assert "db insert kaboom" in raw.error

    @pytest.mark.parametrize(
        "side_effect,expected_error_substr",
        [
            (KeyError("missing_key"), "KeyError"),
            (TypeError("bad type"), "TypeError"),
            (Exception("generic failure"), "Exception"),
        ],
        ids=["key-error", "type-error", "generic-exception"],
    )
    async def test_various_exceptions_captured_in_error_field(
        self,
        seed_user,
        test_client,
        api_token_headers,
        db_session,
        side_effect,
        expected_error_substr,
    ):
        export_req = _build_export_request(
            project_id=str(seed_user.project.project_id),
            user_id=str(seed_user.user.user_id),
        )
        body = export_req.SerializeToString()

        with (
            patch(
                "overmind.api.v1.endpoints.otlp.transformers.transform_spans",
                side_effect=side_effect,
            ),
            pytest.raises(type(side_effect)),
        ):
            await _post_otlp(test_client, api_token_headers, body)

        records = await _get_raw_records(db_session)
        assert len(records) == 1
        assert expected_error_substr in records[0].error
