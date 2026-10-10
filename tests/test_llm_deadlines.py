"""Real sockets reproduce heartbeats that defeat HTTP read timeouts."""

import json
import threading
import time
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from overbae.core.llms import ModelSpec, call_llm, stream_llm_tools
from overbae.core.model_registry import PROVIDERS


@pytest.fixture
def heartbeat_provider():
    disconnected = threading.Event()
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append(body)
            stream = body.get("stream", False)
            backoff = body["messages"][-1]["content"] == "backoff"
            self.send_response(429 if backoff else 200)
            self.send_header("Content-Type", "text/event-stream" if stream else "application/json")
            if backoff:
                self.send_header("Retry-After", "10")
            self.end_headers()
            try:
                if body["messages"][-1]["content"] == "warmup":
                    self.wfile.write(
                        b'{"id":"warmup","choices":[{"index":0,"message":{"role":"assistant","content":"ok"},"finish_reason":"stop"}]}'
                    )
                    return
                if backoff:
                    self.wfile.write(b'{"error":{"message":"busy","type":"rate_limit"}}')
                    return
                if stream and body["messages"][-1]["content"] in {"one-token", "two-tokens"}:
                    chunk = {
                        "id": "first",
                        "object": "chat.completion.chunk",
                        "created": 1,
                        "model": "test",
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"role": "assistant", "content": "hello"},
                                "finish_reason": None,
                            }
                        ],
                    }
                    wire = "data: " + json.dumps(chunk) + "\n\n"
                    if body["messages"][-1]["content"] == "two-tokens":
                        chunk["choices"][0]["delta"]["content"] = "second"
                        wire += "data: " + json.dumps(chunk) + "\n\n"
                    self.wfile.write(wire.encode())
                    self.wfile.flush()
                for _ in range(30):
                    self.wfile.write(b": ping\n\n" if stream else b" ")
                    self.wfile.flush()
                    time.sleep(0.04)
                if stream:
                    self.wfile.write(b"data: [DONE]\n\n")
                else:
                    self.wfile.write(
                        json.dumps(
                            {
                                "id": "completion",
                                "object": "chat.completion",
                                "created": 1,
                                "model": "test",
                                "choices": [
                                    {
                                        "index": 0,
                                        "finish_reason": "stop",
                                        "message": {"role": "assistant", "content": "late"},
                                    }
                                ],
                            }
                        ).encode()
                    )
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                disconnected.set()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1"
        call_llm(
            "warmup",
            model_spec=ModelSpec(
                provider="custom",
                model_id="test",
                base_url=url,
                api_key_env="OPENROUTER_API_KEY",
            ),
            retry_deadline=2,
        )
        requests.clear()
        yield url, disconnected, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.mark.parametrize("stream", [False, True], ids=["json-heartbeats", "sse-heartbeats"])
def test_heartbeats_cannot_extend_completion_deadline(heartbeat_provider, stream):
    url, disconnected, requests = heartbeat_provider
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="deadline"):
        if stream:
            list(
                stream_llm_tools(
                    [{"role": "user", "content": "hello"}],
                    [],
                    provider=replace(PROVIDERS["openrouter"], base_url=url),
                    retry_deadline=0.25,
                )
            )
        else:
            call_llm(
                "hello",
                model_spec=ModelSpec(
                    provider="custom",
                    model_id="test",
                    base_url=url,
                    api_key_env="OPENROUTER_API_KEY",
                ),
                retry_deadline=0.25,
            )
    assert time.monotonic() - started < 0.9
    assert disconnected.wait(timeout=0.5), "deadline left the provider connection open"
    assert len(requests) == 1


def test_retry_after_cannot_extend_completion_deadline(heartbeat_provider):
    url, _, requests = heartbeat_provider
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="deadline"):
        call_llm(
            "backoff",
            model_spec=ModelSpec(
                provider="custom",
                model_id="test",
                base_url=url,
                api_key_env="OPENROUTER_API_KEY",
            ),
            retry_deadline=0.25,
        )
    assert time.monotonic() - started < 0.9
    assert len(requests) == 1


def test_early_consumer_close_closes_provider_stream(heartbeat_provider):
    url, disconnected, requests = heartbeat_provider
    stream = stream_llm_tools(
        [{"role": "user", "content": "one-token"}],
        [],
        provider=replace(PROVIDERS["openrouter"], base_url=url),
        retry_deadline=3,
    )
    assert next(stream).text == "hello"
    started = time.monotonic()
    stream.close()
    assert time.monotonic() - started < 0.5
    assert disconnected.wait(timeout=0.5)
    assert len(requests) == 1


def test_worker_soft_limit_preserves_exception_and_closes_transport(heartbeat_provider):
    import signal

    from celery.exceptions import SoftTimeLimitExceeded

    url, disconnected, _ = heartbeat_provider

    def interrupt(_number, _frame):
        raise SoftTimeLimitExceeded()

    previous = signal.signal(signal.SIGALRM, interrupt)
    try:
        signal.setitimer(signal.ITIMER_REAL, 0.15)
        with pytest.raises(SoftTimeLimitExceeded):
            call_llm(
                "hello",
                model_spec=ModelSpec(
                    provider="custom",
                    model_id="test",
                    base_url=url,
                    api_key_env="OPENROUTER_API_KEY",
                ),
                retry_deadline=3,
            )
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
    assert disconnected.wait(timeout=0.5)


def test_buffered_token_cannot_resume_after_absolute_stream_deadline(heartbeat_provider):
    url, disconnected, _ = heartbeat_provider
    stream = stream_llm_tools(
        [{"role": "user", "content": "two-tokens"}],
        [],
        provider=replace(PROVIDERS["openrouter"], base_url=url),
        retry_deadline=0.25,
    )
    try:
        assert next(stream).text == "hello"
        time.sleep(0.35)
        with pytest.raises(RuntimeError, match="deadline"):
            next(stream)
    finally:
        stream.close()
    assert disconnected.wait(timeout=0.5)
