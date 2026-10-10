"""Real OpenAI instrumentation and OTLP export, confined to localhost."""

import gzip
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import version
from pathlib import Path
from threading import Thread

from opentelemetry.proto.collector.trace.v1.trace_service_pb2 import ExportTraceServiceRequest

SCRIPT = """
import json
import sys
from importlib.metadata import version
import overmind
from openai import OpenAI

overmind.init("local-span-test", overmind_base_url=sys.argv[1],
              service_name="span-io", environment="test", providers=["openai"])
client = OpenAI(api_key="fake-openai", base_url=sys.argv[1] + "/v1", max_retries=0)
with overmind.run("structured-classification", capability_id="11111111-1111-4111-8111-111111111111"):
    response = client.chat.completions.create(
        model="test-model", response_format={"type": "json_object"},
        messages=[{"role": "user", "content": "Classify these shoes as JSON."}],
    )
    assert response.choices[0].message.content == '{"category":"Shoes"}'
overmind.force_flush_traces(timeout_millis=10000)
print(json.dumps({"openai": version("openai")}))
"""


def test_spans(tmp_path):
    exports, requests = [], []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers["Content-Length"]))
            if self.headers.get("Content-Encoding") == "gzip":
                body = gzip.decompress(body)
            requests.append((self.path, self.headers.get("X-Api-Key")))
            if self.path == "/api/v1/traces":
                exports.append(ExportTraceServiceRequest.FromString(body))
                response = b""
            elif self.path == "/v1/chat/completions":
                request = json.loads(body)
                assert request["response_format"] == {"type": "json_object"}
                response = json.dumps({
                    "id": "chatcmpl-local",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "test-model",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {
                                "role": "assistant",
                                "content": '{"category":"Shoes"}',
                            },
                        }
                    ],
                    "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
                }).encode()
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json" if response else "application/x-protobuf")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, *_):
            pass

    host = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=host.serve_forever, daemon=True)
    worker.start()
    env = {key: value for key, value in os.environ.items() if not key.startswith(("OVERMIND_", "OPENAI_", "OTEL_"))}
    env.update({
        "OVERMIND_ANALYTICS_ENABLED": "false",
        "PYTHONPATH": os.pathsep.join([str(Path(__file__).resolve().parents[1]), *sys.path]),
    })
    try:
        result = subprocess.run(
            [sys.executable, "-c", SCRIPT, f"http://127.0.0.1:{host.server_port}"],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        host.shutdown()
        host.server_close()
        worker.join(timeout=2)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout)["openai"] == version("openai")
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
    assert [item.string_value for item in attributes["gen_ai.response.finish_reasons"].values] == ["stop"]
    assert json.loads(attributes["gen_ai.request.structured_output_schema"]) == {"type": "json_object"}
    assert attributes["genai.prompt_tokens"] == 7
    assert attributes["genai.completion_tokens"] == 3
    assert attributes["overmind.capability.id"] == "11111111-1111-4111-8111-111111111111"
    [root] = [span for span in spans if not span.parent_span_id]
    assert llm.trace_id == root.trace_id and llm.parent_span_id == root.span_id
    assert all(key == "local-span-test" for path, key in requests if path == "/api/v1/traces")
    assert all(key is None for path, key in requests if path == "/v1/chat/completions")
