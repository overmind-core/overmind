"""Real sockets reproduce heartbeats that defeat HTTP read timeouts."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from overbae.core.llms import ModelSpec, call_llm


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
            backoff = body["messages"][-1]["content"] == "backoff"
            self.send_response(429 if backoff else 200)
            self.send_header("Content-Type", "application/json")
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
                for _ in range(30):
                    self.wfile.write(b" ")
                    self.wfile.flush()
                    time.sleep(0.04)
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


def test_heartbeats_cannot_extend_completion_deadline(heartbeat_provider):
    url, disconnected, requests = heartbeat_provider
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="deadline"):
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
