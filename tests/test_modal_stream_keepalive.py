import asyncio
import json
from unittest.mock import Mock, patch

import pytest

from overbae.modal.modal_vllm_worker import stream_with_keepalive
from overbae.services.inference_client import (
    ContextBudgetError,
    InferenceClient,
    InferenceClientError,
)


@pytest.mark.asyncio
async def test_gateway_pings_while_waiting_for_worker_headers_and_tokens():
    async def chunks():
        await asyncio.sleep(0.03)
        yield {"status": 200, "headers": {}}
        await asyncio.sleep(0.03)
        yield b'data: {"choices":[]}\n\n'

    stream = stream_with_keepalive(chunks(), interval_s=0.01)
    assert await anext(stream) == b": \n\n"
    result = [chunk async for chunk in stream]
    assert result.count(b": \n\n") >= 2
    assert result[-1] == b'data: {"choices":[]}\n\n'
    assert all(isinstance(chunk, bytes) for chunk in result)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["empty", "http", "exception"])
async def test_gateway_reports_late_failure_as_sse(failure):
    async def chunks():
        if failure == "http":
            yield {"status": 500, "headers": {}}
            yield b"private traceback"
        elif failure == "exception":
            raise RuntimeError("private traceback")

    result = b"".join([chunk async for chunk in stream_with_keepalive(chunks())])
    assert b'"error"' in result
    assert b"private traceback" not in result
    assert result.endswith(b"data: [DONE]\n\n")


@pytest.mark.asyncio
async def test_gateway_disconnect_closes_pending_worker_stream():
    closed = asyncio.Event()

    async def chunks():
        try:
            await asyncio.sleep(60)
            yield {"status": 200, "headers": {}}
        finally:
            closed.set()

    stream = stream_with_keepalive(chunks(), interval_s=0.01)
    await anext(stream)
    await anext(stream)
    await stream.aclose()
    assert closed.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 500])
async def test_nonstream_keepalives_preserve_json_and_late_errors(status):
    async def chunks():
        await asyncio.sleep(0.03)
        yield {"status": status, "headers": {}}
        yield b'{"choices":[]}'

    result = b"".join(
        [chunk async for chunk in stream_with_keepalive(chunks(), interval_s=0.01, sse=False)]
    )
    assert result.startswith(b"\n\n")
    assert json.loads(result) == (
        {"choices": []}
        if status == 200
        else {"error": {"message": "Inference backend error.", "type": "server_error"}}
    )


def test_client_rejects_late_gateway_error_and_does_not_buffer_sse():
    client = InferenceClient(base_url="https://gateway.invalid", api_key="test")
    response = Mock(ok=True)
    response.json.return_value = {"error": {"message": "failed"}}
    with patch("overbae.services.inference_client.requests.post", return_value=response):
        with pytest.raises(InferenceClientError, match="failed"):
            client.chat_completions(model_id="test", messages=[])
        response.iter_lines.return_value = [b'data: {"choices":[]}']
        assert list(client.stream_chat_completions(model_id="test", messages=[]))
    response.iter_lines.assert_called_once_with(chunk_size=1)
    response.close.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("streamed", [False, True])
async def test_worker_context_rejection_survives_committed_gateway_headers(streamed):
    async def chunks():
        yield {"status": 400, "headers": {}}
        yield json.dumps(
            {
                "error": {
                    "message": "Maximum context length exceeded; private provider detail",
                    "type": "BadRequestError",
                }
            }
        ).encode()

    wire = b"".join([chunk async for chunk in stream_with_keepalive(chunks(), sse=streamed)])
    transport_wire = wire
    if streamed:
        assert wire.endswith(b"data: [DONE]\n\n")
        wire = next(line[6:] for line in wire.splitlines() if line.startswith(b"data: {"))
    body = json.loads(wire)
    assert body["error"]["code"] == "context_length_exceeded"
    assert "private provider detail" not in wire.decode()

    client = InferenceClient(base_url="https://gateway.invalid", api_key="test")
    response = Mock(ok=True, status_code=200, json=Mock(return_value=body))
    with (
        patch("overbae.services.inference_client.requests.post", return_value=response),
        pytest.raises(ContextBudgetError, match="reserved output"),
    ):
        if streamed:
            response.iter_lines.return_value = transport_wire.splitlines()
            list(client.stream_chat_completions(model_id="test", messages=[], max_tokens=20000))
        else:
            client.chat_completions(model_id="test", messages=[], max_tokens=20000)


def test_context_classification_preserves_non_object_sse_frames():
    client = InferenceClient(base_url="https://gateway.invalid", api_key="test")
    frames = [b"data: null", b"data: []", b"data: 1", b"data: [DONE]"]
    response = Mock(ok=True, status_code=200)
    response.iter_lines.return_value = frames
    with patch("overbae.services.inference_client.requests.post", return_value=response):
        assert list(client.stream_chat_completions(model_id="test", messages=[])) == [
            frame.decode() + "\n\n" for frame in frames
        ]
    response.close.assert_called_once()
