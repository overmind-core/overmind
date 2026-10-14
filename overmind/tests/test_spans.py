"""The real OpenAI instrumentation and OTLP exporter, against a local fake OpenAI
server and a local OTLP receiver. The SDK runs in a subprocess so its global
tracer provider starts clean."""

import gzip
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from threading import Thread

import pytest

pytest.importorskip("openai")

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest  # noqa: E402

API_KEY = "local-span-test"
CAPABILITY_ID = "11111111-1111-4111-8111-111111111111"

SCRIPT = """
import json
import sys
from importlib.metadata import version

import overmind
from openai import OpenAI

otlp_url, openai_url, api_key, capability_id = sys.argv[1:]
overmind.init(api_key, overmind_base_url=otlp_url, service_name="span-io", environment="test", providers=["openai"])
client = OpenAI(api_key="fake-openai", base_url=openai_url + "/v1", max_retries=0)
with overmind.run("structured-classification", capability_id=capability_id):
    response = client.chat.completions.create(
        model="test-model",
        response_format={"type": "json_object"},
        messages=[{"role": "user", "content": "Classify these shoes as JSON."}],
    )
    assert response.choices[0].message.content == '{"category":"Shoes"}'
overmind.force_flush_traces(timeout_millis=10000)
print(json.dumps({"openai": version("openai")}))
"""

COMPLETION = {
    "id": "chatcmpl-local",
    "object": "chat.completion",
    "created": 1,
    "model": "test-model",
    "choices": [
        {
            "index": 0,
            "finish_reason": "stop",
            "message": {"role": "assistant", "content": '{"category":"Shoes"}'},
        }
    ],
    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
}


def _server(handle):
    seen: list[tuple[str, str | None]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            seen.append((self.path, self.headers.get("X-Api-Key")))
            status, kind, reply = handle(self.path, body)
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(reply)))
            self.end_headers()
            self.wfile.write(reply)

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_openai_spans_reach_the_otlp_receiver_with_capability_and_parentage(tmp_path):
    exports: list[ExportTraceServiceRequest] = []
    chat_requests: list[dict] = []

    def otlp(path, body):
        if path != "/api/v1/traces":
            return 404, "application/json", b"{}"
        exports.append(ExportTraceServiceRequest.FromString(body))
        return 200, "application/x-protobuf", b""

    def openai(path, body):
        if path != "/v1/chat/completions":
            return 404, "application/json", b"{}"
        chat_requests.append(json.loads(body))
        return 200, "application/json", json.dumps(COMPLETION).encode()

    receiver, receiver_seen = _server(otlp)
    provider, provider_seen = _server(openai)
    env = {key: value for key, value in os.environ.items() if not key.startswith(("OVERMIND_", "OPENAI_", "OTEL_"))}
    env.update({
        "OVERMIND_ANALYTICS_ENABLED": "false",
        "PYTHONPATH": os.pathsep.join([str(Path(__file__).resolve().parents[1]), *sys.path]),
    })
    try:
        result = subprocess.run(
            [
                sys.executable,
                "-c",
                SCRIPT,
                f"http://127.0.0.1:{receiver.server_port}",
                f"http://127.0.0.1:{provider.server_port}",
                API_KEY,
                CAPABILITY_ID,
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        receiver.shutdown()
        provider.shutdown()

    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout.splitlines()[-1])["openai"] == version("openai")
    assert [request["response_format"] for request in chat_requests] == [{"type": "json_object"}]

    spans = [
        span
        for export in exports
        for resource in export.resource_spans
        for scope in resource.scope_spans
        for span in scope.spans
    ]
    [llm] = [span for span in spans if any(attr.key == "gen_ai.request.model" for attr in span.attributes)]
    attributes = {attr.key: getattr(attr.value, attr.value.WhichOneof("value")) for attr in llm.attributes}
    assert attributes["gen_ai.request.model"] == "test-model"
    assert json.loads(attributes["gen_ai.request.structured_output_schema"]) == {"type": "json_object"}
    assert attributes["genai.prompt_tokens"] == 7
    assert attributes["genai.completion_tokens"] == 3
    assert attributes["overmind.capability.id"] == CAPABILITY_ID
    [root] = [span for span in spans if not span.parent_span_id]
    assert llm.trace_id == root.trace_id
    assert llm.parent_span_id == root.span_id

    assert ("/api/v1/traces", API_KEY) in receiver_seen
    assert all(key == API_KEY for path, key in receiver_seen if path == "/api/v1/traces")
    assert provider_seen and all(key is None for _, key in provider_seen)
